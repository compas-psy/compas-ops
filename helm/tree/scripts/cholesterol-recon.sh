#!/usr/bin/env bash
# HELM · где в корпусе лежат оба замера холестерина и знает ли система их
# даты. Чтение. Печатается компактно: длинный вывод не помещается в лог.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Оба замера холестерина: документы, значения, даты."""
import re

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.semantic_pilot import source_text
from helm_core.knowledge.temporal import ROLE_DOCUMENT, ROLE_EVENT, find_date_anchors
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (HealthKnowledgeChunk, HealthKnowledgeSourcePrivate,
                              KnowledgeSource)

#: Строка вида «Холестерин общий … 8.4» — имя показателя и число рядом.
VALUE_RE = re.compile(r"(холестерин|липид)[^\n]{0,80}?(\d+[.,]\d+)", re.IGNORECASE)

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

with health_session(tenant) as graph:
    rows = graph.execute(
        select(HealthKnowledgeChunk.source_id, HealthKnowledgeChunk.text,
               HealthKnowledgeSourcePrivate.original_filename)
        .outerjoin(HealthKnowledgeSourcePrivate,
                   HealthKnowledgeChunk.source_id
                   == HealthKnowledgeSourcePrivate.source_id)
        .where(HealthKnowledgeChunk.text.op("~*")("холестерин|липид"))).all()

print(f"############ ЧАНКОВ С «холестерин|липид»: {len(rows)} ############")
by_source: dict = {}
for source_id, text, filename in rows:
    by_source.setdefault((source_id, filename), []).append(text)

for (source_id, filename), texts in by_source.items():
    source = session.get(KnowledgeSource, source_id)
    whole = source_text(source) if source is not None else None
    anchors = find_date_anchors(whole) if whole else []
    dated = [(a.value, a.role) for a in anchors
             if a.role in (ROLE_DOCUMENT, ROLE_EVENT)][:3]
    values = []
    for text in texts:
        values += [f"{m.group(1)}…{m.group(2)}" for m in VALUE_RE.finditer(text)]
    print(f"\n{filename}")
    print(f"  чанков: {len(texts)}   даты документа: {dated or 'НЕТ'}")
    print(f"  загружен: {source.created_at:%d.%m.%Y}" if source else "  источник не найден")
    for value in values[:6]:
        print(f"  значение: {value[:80]}")
    if not values:
        print("  значений рядом со словом не найдено")
PYEOF
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi
echo "############ ГОТОВО ############"
