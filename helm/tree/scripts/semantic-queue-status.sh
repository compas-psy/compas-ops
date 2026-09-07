#!/usr/bin/env bash
# HELM · что стоит в очереди семантического разбора и как далеко дошло.
# Только чтение.
#
# ЗАЧЕМ ЭТОТ СКРИПТ ВМЕСТО ПРЕЖНЕГО. `semantic-pause-oversized.sh`
# останавливал разом ВСЕ незавершённые задания источников с расширением
# fb2 — чтобы снять одну книгу. Такая выборка попадает и в задания, о
# которых оператор не думал, и в те, которых ещё нет: следующая
# загруженная fb2 попала бы под тот же признак. Скрипт удалён.
#
# Остановка одного задания теперь называет это одно задание по
# идентификатору — `semantic_jobs.pause_semantic_job(job_id=..., reason=...)`.
# Идентификатор берётся отсюда.
#
# Переразбор одного источника — `semantic_jobs.request_rederivation()`.
# Штатный переразбор корпуса — подъём `SEMANTIC_VERSION`; забыть его при
# изменении парсера или чанкинга не даёт `derivation.py` и его тест.
# Правка статусов руками перестала быть способом делать и то и другое.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.derivation import derivation_fingerprint
from helm_core.knowledge.semantic_publish import SEMANTIC_VERSION
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    KnowledgeSemanticJob, KnowledgeSemanticRun, KnowledgeSemanticWindow, KnowledgeSource,
)

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)

print(f"############ ВЕРСИЯ РАЗБОРА ############")
print(f"  SEMANTIC_VERSION: {SEMANTIC_VERSION}")
print(f"  отпечаток кода разбора: {derivation_fingerprint()[:16]}…")

print()
print("############ ЗАДАНИЯ ############")
rows = session.execute(
    select(KnowledgeSemanticJob.status, func.count())
    .group_by(KnowledgeSemanticJob.status)).all()
for status, count in rows:
    print(f"  {status}: {count}")

print()
print("############ НЕЗАВЕРШЁННЫЕ — ПОШТУЧНО ############")
unfinished = session.scalars(
    select(KnowledgeSemanticJob)
    .where(KnowledgeSemanticJob.status.in_(("pending", "running")))
    .order_by(KnowledgeSemanticJob.created_at)).all()
if not unfinished:
    print("  нет")
for job in unfinished:
    source = session.get(KnowledgeSource, job.source_id)
    name = (source.original_filename or source.raw_path or "?") if source else "?"
    done = total = 0
    if job.semantic_run_id:
        run = session.get(KnowledgeSemanticRun, job.semantic_run_id)
        if run is not None:
            total = run.windows_total
            done = session.scalar(
                select(func.count()).select_from(KnowledgeSemanticWindow)
                .where(KnowledgeSemanticWindow.semantic_run_id == run.id,
                       KnowledgeSemanticWindow.parent_window_id.is_(None))) or 0
    print(f"  id={job.id}")
    print(f"    источник: {name}")
    print(f"    статус={job.status} попыток={job.attempts} аренда={job.lease_expires_at}")
    print(f"    окон верхнего уровня разобрано: {done} (всего окон прогона: {total})")
    print(f"    ошибка: {job.error or '—'}")

print()
print("############ ПОСЛЕДНИЕ РЕВИЗИИ ############")
for run in session.scalars(
        select(KnowledgeSemanticRun)
        .order_by(KnowledgeSemanticRun.created_at.desc()).limit(5)).all():
    print(f"  {run.status} v{run.semantic_version} окон {run.windows_processed}"
          f"/{run.windows_total} провалено {run.windows_failed}"
          f" покрытие {run.coverage_ratio} узлов {run.nodes_created}")
PYEOF
