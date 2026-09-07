#!/usr/bin/env bash
# HELM · довести переразбор книги до конца: освободить воркер и дать
# заданию выполниться.
#
# ЧТО ВЫЯСНИЛА РАЗВЕДКА 433. Задание на переразбор так и осталось
# `pending`: воркер занят semantic-заданием по этой же книге. Цикл
# `run_forever()` берёт ingest раньше semantic, но выйти из уже начатого
# semantic-разбора он не может, а разбор идёт по фрагменту в 1 441 314
# символов. Аренда 30 минут, попытка 3 из 3 — то есть задание уже дважды
# не доживало до конца. Один переросший источник держит всю очередь:
# следующая загрузка владельца встала бы за ним.
#
# ДВА ДЕЙСТВИЯ, каждое со своей причиной:
#
#   перезапуск воркера — освобождает процесс от разбора, который всё
#   равно не завершится: он идёт по тексту, который мы сейчас заменим;
#
#   сброс semantic-задания в `pending` — иначе переразбор не получит
#   нового. `enqueue_semantic()` ключуется четвёркой «пользователь,
#   источник, содержимое, версия», а байты файла те же: старое задание
#   занимает ключ, и `on_conflict_do_nothing` промолчит. Поэтому не
#   новое задание, а то же самое, возвращённое в очередь — оно и
#   разберёт новые фрагменты.
#
# Ничего не удаляется. Оригинал файла на месте, фрагменты заменяет
# `store_chunks()` своим обычным путём, semantic-строка остаётся той же.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. ЧТО ЕСТЬ СЕЙЧАС ############"
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSemanticJob, KnowledgeSource

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)

for job_id, status, attempts, lease, error, filename in session.execute(
        select(KnowledgeSemanticJob.id, KnowledgeSemanticJob.status,
               KnowledgeSemanticJob.attempts, KnowledgeSemanticJob.lease_expires_at,
               KnowledgeSemanticJob.error, KnowledgeSource.original_filename)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeSemanticJob.source_id)
        .where(KnowledgeSource.raw_path.ilike("%.fb2"))).all():
    print(f"  semantic {job_id} | {status} | попыток={attempts} | аренда до {lease}")
    print(f"    источник: {filename or 'без имени'} | ошибка: {error or '—'}")
PYEOF

echo
echo "############ 2. ОСТАНОВИТЬ ВОРКЕР ############"
sudo docker compose stop helm-knowledge-worker

echo
echo "############ 3. ВЕРНУТЬ SEMANTIC-ЗАДАНИЕ В ОЧЕРЕДЬ ############"
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeIngestStatus, KnowledgeSemanticJob, KnowledgeSource

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)

source_ids = session.scalars(
    select(KnowledgeSource.id).where(KnowledgeSource.raw_path.ilike("%.fb2"))).all()
touched = session.execute(
    update(KnowledgeSemanticJob)
    .where(KnowledgeSemanticJob.source_id.in_(source_ids))
    .values(status=KnowledgeIngestStatus.PENDING, attempts=0,
            lease_expires_at=None, error=None)).rowcount
session.commit()
print(f"  возвращено в очередь semantic-заданий: {touched}")
PYEOF

echo
echo "############ 4. ЗАПУСТИТЬ ВОРКЕР ############"
sudo docker compose start helm-knowledge-worker

echo
echo "############ 5. ЖДЁМ ПЕРЕРАЗБОР (до 10 минут) ############"
for _ in $(seq 1 60); do
  sleep 10
  line=$(sudo docker compose exec -T helm-core python3 -c "
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeIngestJob, KnowledgeSource
session = sessionmaker(bind=create_engine(get_settings().database_url))()
bind_knowledge_user(session, None)
sid = session.scalar(select(KnowledgeSource.id).where(KnowledgeSource.raw_path.ilike('%.fb2')))
job = session.execute(select(KnowledgeIngestJob.status).where(
    KnowledgeIngestJob.source_id == sid).order_by(
    KnowledgeIngestJob.created_at.desc()).limit(1)).scalar()
parser = session.scalar(select(KnowledgeSource.parser).where(KnowledgeSource.id == sid))
n = session.scalar(select(func.count()).select_from(KnowledgeChunk).where(
    KnowledgeChunk.source_id == sid))
print(f'{job}|{parser}|{n}')
" 2>/dev/null | tr -d '\r')
  echo "  задание|парсер|фрагментов: ${line:-?}"
  case "$line" in done\|*) break ;; failed\|*) break ;; esac
done

echo
echo "############ 6. ЧТО ПОЛУЧИЛОСЬ ############"
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (KnowledgeChunk, KnowledgeIngestJob, KnowledgeSemanticJob,
                              KnowledgeSource)

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)

for source_id, filename, parser, status in session.execute(
        select(KnowledgeSource.id, KnowledgeSource.original_filename,
               KnowledgeSource.parser, KnowledgeSource.status)
        .where(KnowledgeSource.raw_path.ilike("%.fb2"))).all():
    n, total, longest = session.execute(
        select(func.count(), func.sum(func.length(KnowledgeChunk.text)),
               func.max(func.length(KnowledgeChunk.text)))
        .where(KnowledgeChunk.source_id == source_id)).one()
    with_vec = session.scalar(
        select(func.count()).select_from(KnowledgeChunk)
        .where(KnowledgeChunk.source_id == source_id,
               KnowledgeChunk.embedding.isnot(None)))
    print(f"  {filename or 'без имени'} | parser={parser} | {status}")
    print(f"    фрагментов={n} | с вектором={with_vec} | символов={total or 0} | самый длинный={longest or 0}")
    for text in session.scalars(
            select(KnowledgeChunk.text).where(KnowledgeChunk.source_id == source_id)
            .order_by(KnowledgeChunk.ordinal).limit(4)).all():
        print(f"    фрагмент: {text[:170]!r}")
    ingest = session.execute(
        select(KnowledgeIngestJob.status, KnowledgeIngestJob.error)
        .where(KnowledgeIngestJob.source_id == source_id)
        .order_by(KnowledgeIngestJob.created_at.desc()).limit(1)).first()
    semantic = session.execute(
        select(KnowledgeSemanticJob.status, KnowledgeSemanticJob.attempts)
        .where(KnowledgeSemanticJob.source_id == source_id)).all()
    print(f"    ingest-задание: {ingest}")
    print(f"    semantic-задания: {semantic}")
PYEOF
