#!/usr/bin/env bash
# HELM · гейт универсальности, этап 5: готовы ли срезы к прогону.
#
# Только чтение. Отвечает на один вопрос: по каким источникам ответ уже
# может быть структурным, а по каким ещё нет. Срез B ждёт окончания
# семантического разбора MASTER_TZ.md — пока разбора нет, вопрос по
# проектному документу пойдёт обычным поиском, и выдать это за проход
# гейта было бы подлогом.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

dc() { sudo docker compose exec -T helm-core "$@"; }

echo
echo "############ ИСТОЧНИКИ СРЕЗОВ ############"
dc python3 - <<'PYEOF'
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.models import (KnowledgeChunk, KnowledgeIngestJob, KnowledgeSemanticJob,
                              KnowledgeSemanticWindow, KnowledgeSource)
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)

for name in ("MASTER_TZ.md",):
    source = session.scalars(
        select(KnowledgeSource)
        .where(KnowledgeSource.original_filename == name)
        .order_by(KnowledgeSource.created_at.desc()).limit(1)).one_or_none()
    if source is None:
        print(f"  {name}: источника нет")
        continue
    chunks = session.scalar(select(func.count()).select_from(KnowledgeChunk)
                            .where(KnowledgeChunk.source_id == source.id))
    print(f"  {name}: домен {source.domain}, статус {source.status}, чанков {chunks}")
    print(f"    семантический прогон источника: {source.current_semantic_run_id}")
    for job in session.scalars(select(KnowledgeIngestJob)
                               .where(KnowledgeIngestJob.source_id == source.id)).all():
        print(f"    разбор: {job.status}  ошибка: {job.error or '—'}")
    for job in session.scalars(select(KnowledgeSemanticJob)
                               .where(KnowledgeSemanticJob.source_id == source.id)).all():
        windows = session.scalar(
            select(func.count()).select_from(KnowledgeSemanticWindow)
            .where(KnowledgeSemanticWindow.semantic_run_id == job.semantic_run_id)
        ) if job.semantic_run_id else 0
        print(f"    семантика: {job.status}  попыток: {job.attempts}  окон: {windows}"
              f"  ошибка: {job.error or '—'}")

print()
print("  очередь семантики целиком:")
for status, count in session.execute(
        select(KnowledgeSemanticJob.status, func.count())
        .group_by(KnowledgeSemanticJob.status)).all():
    print(f"    {status}: {count}")
PYEOF

echo
echo "############ ЧТО УЖЕ УМЕЕТ СТРУКТУРНЫЙ ИСПОЛНИТЕЛЬ ############"
dc python3 - <<'PYEOF'
from helm_core.knowledge import query_router as qr

print(f"  спек в модуле: {[n for n in dir(qr) if isinstance(getattr(qr, n), qr.StructuralSpec)]}")
print(f"  врачебная спека: предмет {qr.DOCTORS.subject.entity_types}, "
      f"ребро {qr.DOCTORS.edge.relation_type}/{qr.DOCTORS.edge.role}")
import inspect
for name in ("_answer_in", "_graph_items", "_evidence_items"):
    default = inspect.signature(getattr(qr, name)).parameters["spec"].default
    print(f"  {name}: спека по умолчанию — "
          f"{'нет' if default is inspect.Parameter.empty else default}")
PYEOF
