#!/usr/bin/env bash
# HELM · почему на «последний холестерин» пришёл не последний. Чтение.
#
# Владелец 07.09.2026: сдавал дважды за неделю, бот дважды выдал 8.1 из
# первого анализа, а в липидном профиле было 8.4. Вопрос про ВРЕМЯ, и
# проверяется здесь именно оно: есть ли в корпусе второе значение, знает
# ли система даты своих документов и в каком порядке отдаёт кандидатов.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Холестерин в корпусе: значения, документы, даты, порядок выдачи."""
import re

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.probe import probe
from helm_core.knowledge.semantic_pilot import source_text
from helm_core.knowledge.temporal import find_date_anchors
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource
from helm_core.models.health import HealthKnowledgeChunk, HealthKnowledgeSourcePrivate

VALUE_RE = re.compile(r"холестерин[^\n]{0,120}", re.IGNORECASE)

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

print("############ 1. ГДЕ В КОРПУСЕ ХОЛЕСТЕРИН ############")
if health_schema_configured():
    with health_session(tenant) as graph:
        rows = graph.execute(
            select(HealthKnowledgeChunk.id, HealthKnowledgeChunk.source_id,
                   HealthKnowledgeChunk.text,
                   HealthKnowledgeSourcePrivate.original_filename)
            .outerjoin(HealthKnowledgeSourcePrivate,
                       HealthKnowledgeChunk.source_id
                       == HealthKnowledgeSourcePrivate.source_id)
            .where(HealthKnowledgeChunk.text.ilike("%холестерин%"))).all()
    print(f"  чанков со словом «холестерин»: {len(rows)}")
    for chunk_id, source_id, text, filename in rows:
        matches = VALUE_RE.findall(text)
        print(f"\n  — {filename}")
        print(f"    чанк {chunk_id}")
        for line in matches[:4]:
            print(f"    значение: {line.strip()[:110]}")
        anchors = find_date_anchors(text)
        print(f"    даты В ЧАНКЕ: {[(a.value, a.role) for a in anchors] or 'нет'}")
        source = session.get(KnowledgeSource, source_id)
        if source is not None:
            whole = source_text(source)
            doc_anchors = find_date_anchors(whole) if whole else []
            print(f"    даты В ДОКУМЕНТЕ: "
                  f"{[(a.value, a.role) for a in doc_anchors][:6] or 'нет'}")
            print(f"    загружен: {source.created_at:%d.%m.%Y %H:%M}")

print("\n############ 2. ЧТО ОТДАЁТ ПОИСК ############")
for question in ("Какой у меня холестерин был в последний раз?",
                 "Уровень холестерина по липидному профилю?"):
    tenant = bind_knowledge_user(session, None)
    result = probe(session, query=question)
    print(f"\n  вопрос: {question}")
    print(f"  исход: {result.outcome}  режим: {result.mode}")
    print(f"  ответ: {(result.answer_text or '')[:300]}")
    for item in result.evidence:
        print(f"    кандидат: {item.original_filename} · ранг {item.rank:.4f}")
        print(f"      {item.chunk_text[:100]!r}")
    session.rollback()

session.rollback()
PYEOF
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi
echo "############ ГОТОВО ############"
