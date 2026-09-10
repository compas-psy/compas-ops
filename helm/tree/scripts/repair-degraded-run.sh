#!/usr/bin/env bash
# HELM · починить деградировавшую ревизию повтором провалившихся окон.
#
# ЗАЧЕМ. Решение владельца 10.09.2026, путь 1. У `MASTER_TZ.md` одно окно
# из 472 упало с `EXTRACTION_FAILED`, ревизия вышла DEGRADED, и документ
# не попал в структурный слой вовсе: `current_semantic_run_id`
# переключается только на READY. Ревизия закончена ДО появления повтора —
# задание уже `done`, воркер его не возьмёт, само оно не починится.
#
# ЧТО ЭТО НЕ ДЕЛАЕТ. Не переписывает статусы руками и не опускает порог.
# Зовётся `repair_degraded_run()`: повтор идёт в ту же строку окна, а
# судьбу ревизии решает тот же `_finalize()`, что и обычный разбор.
# Окно, падающее всегда, останется провалившимся, ревизия — DEGRADED, и
# текущей она не станет.
#
# Модель зовётся ровно на провалившиеся окна — не на весь источник.
set -uo pipefail
cd /opt/helm/compose || exit 1

SOURCE_NAME="${1:-MASTER_TZ.md}"

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "источник: $SOURCE_NAME"

dc() { sudo docker compose exec -T helm-core "$@"; }

echo
echo "############ ДО ПОЧИНКИ ############"
dc python3 - "$SOURCE_NAME" <<'PYEOF'
import sys
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.models import KnowledgeSemanticRun, KnowledgeSource
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url,
                                          pool_pre_ping=True))()
bind_knowledge_user(session, None)
source = session.scalars(
    select(KnowledgeSource).where(KnowledgeSource.original_filename == sys.argv[1])
    .order_by(KnowledgeSource.created_at.desc()).limit(1)).one_or_none()
if source is None:
    print("  источника нет")
    raise SystemExit(0)
print(f"  текущая ревизия источника: {source.current_semantic_run_id}")
run = session.scalars(
    select(KnowledgeSemanticRun)
    .where(KnowledgeSemanticRun.source_id == source.id)
    .order_by(KnowledgeSemanticRun.created_at.desc()).limit(1)).one_or_none()
if run is not None:
    print(f"  последняя ревизия: {run.status}  код: {run.error_code or '—'}  "
          f"окон {run.windows_total}, провалено {run.windows_failed}, "
          f"узлов {run.nodes_created}, рёбер {run.edges_created}, "
          f"покрытие {run.coverage_ratio}")
PYEOF

echo
echo "############ ПОЧИНКА ############"
dc python3 - "$SOURCE_NAME" <<'PYEOF'
import sys
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.semantic_pilot import source_text
from helm_core.knowledge.semantic_publish import repair_degraded_run
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource

session = sessionmaker(bind=create_engine(get_settings().database_url,
                                          pool_pre_ping=True))()
bind_knowledge_user(session, None)
source = session.scalars(
    select(KnowledgeSource).where(KnowledgeSource.original_filename == sys.argv[1])
    .order_by(KnowledgeSource.created_at.desc()).limit(1)).one_or_none()
if source is None:
    print("  источника нет — чинить нечего")
    raise SystemExit(0)

text = source_text(source)
if text is None:
    print("  текст источника недоступен — чинить не по чему")
    raise SystemExit(0)

result = repair_degraded_run(session, source=source, text=text)
if result is None:
    print("  ничего не изменилось: чинить нечего либо отказ постоянный")
    session.rollback()
    raise SystemExit(0)

session.commit()
print(f"  ревизия: {result.status}")
print(f"  переключена текущей: {result.switched}")
print(f"  окон {result.windows_total}, провалено {result.windows_failed}, "
      f"узлов {result.nodes_created}, рёбер {result.edges_created}, "
      f"покрытие {result.coverage_ratio}")
PYEOF

echo
echo "############ ПОСЛЕ ПОЧИНКИ ############"
dc python3 - "$SOURCE_NAME" <<'PYEOF'
import sys
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.models import KnowledgeSource
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url,
                                          pool_pre_ping=True))()
bind_knowledge_user(session, None)
source = session.scalars(
    select(KnowledgeSource).where(KnowledgeSource.original_filename == sys.argv[1])
    .order_by(KnowledgeSource.created_at.desc()).limit(1)).one_or_none()
print(f"  текущая ревизия источника: "
      f"{source.current_semantic_run_id if source else 'источника нет'}")
PYEOF
