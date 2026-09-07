#!/usr/bin/env bash
# HELM · где сейчас переразбор книги: состояние задания и фрагменты.
# Только чтение.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeIngestJob, KnowledgeSource

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
bind_knowledge_user(session, None)

print("############ ЗАДАНИЯ ПОСЛЕДНЕГО ЧАСА ############")
for job_id, status, error, created, updated, filename in session.execute(
        select(KnowledgeIngestJob.id, KnowledgeIngestJob.status, KnowledgeIngestJob.error,
               KnowledgeIngestJob.created_at, KnowledgeIngestJob.updated_at,
               KnowledgeSource.original_filename)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeIngestJob.source_id)
        .order_by(KnowledgeIngestJob.created_at.desc()).limit(5)).all():
    print(f"  {status:<12} | {created} → {updated} | {filename or 'без имени'}")
    if error:
        print(f"    ошибка: {error[:300]}")

print()
print("############ ФРАГМЕНТЫ .fb2 ############")
for source_id, filename, parser in session.execute(
        select(KnowledgeSource.id, KnowledgeSource.original_filename, KnowledgeSource.parser)
        .where(KnowledgeSource.raw_path.ilike("%.fb2"))).all():
    n, total, longest = session.execute(
        select(func.count(), func.sum(func.length(KnowledgeChunk.text)),
               func.max(func.length(KnowledgeChunk.text)))
        .where(KnowledgeChunk.source_id == source_id)).one()
    with_vec = session.scalar(
        select(func.count()).select_from(KnowledgeChunk)
        .where(KnowledgeChunk.source_id == source_id,
               KnowledgeChunk.embedding.isnot(None)))
    print(f"  {filename or 'без имени'} | parser={parser}")
    print(f"    фрагментов={n} | с вектором={with_vec} | символов={total or 0} | самый длинный={longest or 0}")
    for text in session.scalars(
            select(KnowledgeChunk.text).where(KnowledgeChunk.source_id == source_id)
            .order_by(KnowledgeChunk.ordinal).limit(3)).all():
        print(f"    фрагмент: {text[:140]!r}")

print()
print("############ ВОРКЕР ЖИВ? ############")
print("последние 15 строк лога — ниже, отдельной командой")
PYEOF

echo
sudo docker compose logs --tail=15 helm-knowledge-worker 2>&1 | tail -20
