#!/usr/bin/env bash
# HELM · как разобрана таблица лабораторного PDF. Только чтение.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Текст липидного профиля: чем разобран и как легли колонки таблицы."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_session
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    HealthKnowledgeChunk, HealthKnowledgeSourcePrivate, KnowledgeSource,
)

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
app = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(app, None)

with health_session(tenant) as session:
    source_id = session.scalars(
        select(HealthKnowledgeSourcePrivate.source_id)
        .where(HealthKnowledgeSourcePrivate.original_filename.like("148967020%"))
    ).first()
    chunks = list(session.scalars(
        select(HealthKnowledgeChunk).where(HealthKnowledgeChunk.source_id == source_id)
        .order_by(HealthKnowledgeChunk.ordinal)
    ).all())

envelope = app.get(KnowledgeSource, source_id)
print("############ ИСТОЧНИК ############")
print(f"  парсер: {envelope.parser}  статус: {envelope.status}  дата: {envelope.content_date}")
print(f"  чанков: {len(chunks)}")

full = "\n".join(c.text for c in chunks)
start = max(full.find("Липидный профиль"), 0)
print("############ ТЕКСТ ВОКРУГ ЛИПИДНОГО ПРОФИЛЯ ############")
print(full[start:start + 2000])
print("############ ГДЕ ХОЛЕСТЕРИН ############")
for chunk in chunks:
    if "олестерин" in chunk.text:
        head = chunk.text.replace("\n", " ⏎ ")[:200]
        print(f"  чанк {chunk.ordinal}: {head}")
app.rollback()
PYEOF
rc=$?
if [ "$rc" -ne 0 ]; then echo "############ ПРОВАЛ (код $rc) ############"; exit "$rc"; fi
echo "############ ГОТОВО ############"
