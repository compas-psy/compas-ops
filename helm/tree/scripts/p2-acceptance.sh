#!/usr/bin/env bash
# HELM · приёмка P2: загрузка сама доходит до знания.
#
# ЗАПУСКАТЬ через action=maintenance: пишет (регистрирует источник и
# ждёт, пока воркер его разберёт).
#
# Что доказывается и чего НЕ доказывается. Файл регистрируется той же
# функцией `register_file_for_ingest()`, которой пользуется приём из
# чата, но не через сам чат: клиента пользователя Telegram у агента нет
# (Bot API не умеет писать своему же боту от лица человека). Значит
# доказывается путь «источник зарегистрирован → L1 → семантическая
# ревизия», а транспорт остаётся непроверенным. Так и записывается.
#
# Ключевое условие: НИ ОДНОЙ команды backfill. Если ревизия появилась —
# её сделал воркер сам.
#
# Файл нарочно немедицинский и синтетический: приёмка проводки не
# нуждается в личных данных.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 0. СХЕМА НА МЕСТЕ ############"
echo -n "  alembic head в базе: "
sudo docker compose exec -T helm-core alembic current 2>/dev/null | tail -1 || echo "неизвестно"
echo -n "  таблица очереди:     "
sudo docker compose exec -T postgres psql -U helm -d helm -tAc \
  "select to_regclass('public.knowledge_semantic_jobs') is not null" 2>/dev/null \
  | tr -d ' ' | sed 's/^t$/есть/; s/^f$/НЕТ/' || echo "не проверено"
echo

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Регистрируем файл и ждём, пока воркер доведёт его до ревизии."""
import time
import uuid
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.ingest import register_file_for_ingest
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (KnowledgeIngestJob, KnowledgeSemanticJob,
                              KnowledgeSource)

TEXT = """# Заметка о стенде сборки {marker}

Решение от 6 сентября 2026: сборки под Linux выполняет локальный раннер,
сборки под Windows остаются в GitHub. Причина — раннер не имеет доступа
к боевым секретам, и держать там вторую платформу незачем.

Ответственный за раннер — команда инфраструктуры. Ограничение по памяти
для одного задания: два гигабайта.
"""

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)

# `register_file_for_ingest()` сохраняет raw_path КАК ЕСТЬ
# (ingest.py:227), а читает его другой контейнер — воркер. Значит файл
# обязан лежать в общем томе `/opt/helm-knowledge`, одинаковом внутри
# обоих (docker-compose.yml). Прогон 350 упал именно здесь: файл лежал
# в /tmp контейнера helm-core, воркер получил FileNotFoundError, и до
# очереди дело не дошло вовсе.
marker = uuid.uuid4().hex[:8]
staging = Path("/opt/helm-knowledge/acceptance")
staging.mkdir(parents=True, exist_ok=True)
path = staging / f"p2-acceptance-{marker}.md"
# Маркер В ТЕКСТЕ, а не только в имени: дедуп идёт по SHA256 содержимого
# (ingest.py:205-212). С одинаковым текстом следующий прогон подцепил бы
# источник предыдущего вместо нового.
text = TEXT.replace("{marker}", marker)
path.write_text(text, encoding="utf-8")

with Session() as session:
    tenant = bind_knowledge_user(session, None)
    result = register_file_for_ingest(
        session, domain="ops", raw_path=path,
        original_filename=f"стенд-сборки-{marker}.md", mime_type="text/markdown")
    session.commit()
    source_id = result.source.id
    print(f"  источник зарегистрирован: {str(source_id)[:8]}…  создан={result.created}")

deadline = time.monotonic() + 900
ingest_status = semantic_status = current_run = None
while time.monotonic() < deadline:
    with Session() as session:
        bind_knowledge_user(session, tenant)
        ingest_status = session.scalar(
            select(KnowledgeIngestJob.status)
            .where(KnowledgeIngestJob.source_id == source_id))
        semantic_status = session.scalar(
            select(KnowledgeSemanticJob.status)
            .where(KnowledgeSemanticJob.source_id == source_id))
        current_run = session.scalar(
            select(KnowledgeSource.current_semantic_run_id)
            .where(KnowledgeSource.id == source_id))
    if current_run is not None or semantic_status in ("failed",):
        break
    time.sleep(10)

print(f"  L1-задание:        {ingest_status}")
print(f"  задание разбора:   {semantic_status}")
print(f"  текущая ревизия:   {'есть' if current_run else 'НЕТ'}")

with Session() as session:
    bind_knowledge_user(session, tenant)
    if current_run is not None:
        from helm_core.knowledge.semantic_publish import PUBLIC_MODELS
        from sqlalchemy import func
        nodes = session.scalar(
            select(func.count()).select_from(PUBLIC_MODELS.node)
            .where(PUBLIC_MODELS.node.semantic_run_id == current_run))
        print(f"  узлов в ревизии:   {nodes}")

    # Повтор тех же байтов не должен породить второе задание.
    path.write_text(text, encoding="utf-8")
    again = register_file_for_ingest(
        session, domain="ops", raw_path=path,
        original_filename=f"стенд-сборки-{marker}.md", mime_type="text/markdown")
    session.commit()
    # Привязка тенанта транзакционно-локальная (`set_config(..., true)`,
    # tenancy.py:61): после commit она снята, и RLS спрячет от нас
    # собственные строки. Прогон 351 напечатал из-за этого «заданий 0»
    # там, где задание было и отработало. Привязываемся заново.
    bind_knowledge_user(session, tenant)
    jobs = session.scalars(
        select(KnowledgeSemanticJob.id)
        .where(KnowledgeSemanticJob.source_id == source_id)).all()
    print(f"  повторная загрузка: created={again.created}, заданий разбора {len(jobs)}")

path.unlink(missing_ok=True)

print()
if current_run is not None:
    print("  ПРИЁМКА P2: ревизия появилась БЕЗ единой команды backfill.")
else:
    print("  ПРИЁМКА P2: ПРОВАЛ — ревизии нет, смотреть журнал воркера.")
PYEOF
RC=$?

echo
echo "############ ЖУРНАЛ ВОРКЕРА ############"
sudo docker compose logs --tail 15 helm-knowledge-worker 2>/dev/null \
  | grep -iE "semantic|ingest job" | tail -10 | sed 's/^/  /' || echo "  журнал пуст"

echo "############ ГОТОВО (rc=$RC) ############"
exit "$RC"
