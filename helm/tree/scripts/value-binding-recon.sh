#!/usr/bin/env bash
# HELM · к чему на самом деле привязаны числа, которые ответ выдаёт за
# холестерин. Только чтение.
#
# ЗАЧЕМ. Прогон 435 ответил «5.7 ммоль/л», прогон 429 — «8.4 ммоль/л».
# Оба прошли проверку заземления, значит оба числа во фрагментах ЕСТЬ.
# Вопрос не «есть ли число», а «чьё оно». Печатаются строки источников,
# где эти числа стоят, вместе с тем, что стоит перед ними.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Строки с 5.7, 8.4, 6.2 и словом «холестерин» — как они лежат."""
import re

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import HealthKnowledgeChunk, HealthKnowledgeSourcePrivate

WANTED = ("5.7", "5,7", "8.4", "8,4", "6.2", "6,2")

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

if not health_schema_configured():
    print("health-схема не настроена — смотреть нечего")
    raise SystemExit

with health_session(tenant) as graph:
    rows = graph.execute(
        select(HealthKnowledgeChunk.text, HealthKnowledgeSourcePrivate.original_filename)
        .outerjoin(HealthKnowledgeSourcePrivate,
                   HealthKnowledgeChunk.source_id
                   == HealthKnowledgeSourcePrivate.source_id)
        .where(HealthKnowledgeChunk.text.op("~*")("холестерин|липид"))).all()

print(f"############ ЧАНКОВ С «холестерин|липид»: {len(rows)} ############")
for text, filename in rows:
    lines = text.splitlines()
    hits = [(i, line) for i, line in enumerate(lines)
            if any(value in line for value in WANTED)
            or re.search("холестерин|липид", line, re.IGNORECASE)]
    if not hits:
        continue
    print()
    print(f"— {filename or 'без имени'} (строк в чанке: {len(lines)})")
    shown = set()
    for i, _ in hits:
        for j in range(max(0, i - 1), min(len(lines), i + 2)):
            if j in shown:
                continue
            shown.add(j)
            print(f"   [{j:>3}] {lines[j][:200]!r}")
PYEOF
