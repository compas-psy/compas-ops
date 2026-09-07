#!/usr/bin/env bash
# HELM · обслуживание: перечитать книги в fb2 новым разбором.
#
# Запись обратимая и производная: воркер разберёт тот же самый файл из
# raw_path заново, `store_chunks()` заменит фрагменты этого источника
# (сначала удаляет прежние — так и задумано, это же делает пересборка
# поискового слоя), semantic-разбор встанет в очередь своим заданием.
# Оригинал файла не трогается вовсе.
#
# Задание заводится БЕЗ channel/recipient: владельцу в бота ничего не
# уходит, это техническая перезагрузка, а не его загрузка.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Переразбор источников .fb2, разобранных прежним парсером."""
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeIngestJob, KnowledgeIngestStatus, KnowledgeSource

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

rows = session.execute(
    select(KnowledgeSource.id, KnowledgeSource.original_filename, KnowledgeSource.parser)
    .where(KnowledgeSource.raw_path.ilike("%.fb2"),
           func.coalesce(KnowledgeSource.parser, "") != "fb2")).all()

print(f"############ К ПЕРЕРАЗБОРУ: {len(rows)} ############")
for source_id, filename, parser in rows:
    chunks = session.scalar(select(func.count()).select_from(KnowledgeChunk)
                            .where(KnowledgeChunk.source_id == source_id))
    print(f"  {filename or 'без имени'} | было: parser={parser}, фрагментов={chunks}")
    session.add(KnowledgeIngestJob(knowledge_user_id=tenant, source_id=source_id,
                                   status=KnowledgeIngestStatus.PENDING))
session.commit()
print("задания поставлены в очередь" if rows else "нечего переразбирать")
PYEOF

echo
echo "############ ЖДЁМ ВОРКЕР (до 5 минут) ############"
for _ in $(seq 1 30); do
  sleep 10
  pending=$(sudo docker compose exec -T helm-core python3 -c "
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeIngestJob, KnowledgeIngestStatus
session = sessionmaker(bind=create_engine(get_settings().database_url))()
bind_knowledge_user(session, None)
print(session.scalar(select(func.count()).select_from(KnowledgeIngestJob).where(
    KnowledgeIngestJob.status.in_([KnowledgeIngestStatus.PENDING,
                                   KnowledgeIngestStatus.RUNNING]))))
" 2>/dev/null | tr -d '\r')
  echo "  заданий в работе: ${pending:-?}"
  [ "${pending:-1}" = "0" ] && break
done

echo
echo "############ ЧТО ПОЛУЧИЛОСЬ ############"
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeSource

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
bind_knowledge_user(session, None)

for source_id, filename, parser, status in session.execute(
        select(KnowledgeSource.id, KnowledgeSource.original_filename,
               KnowledgeSource.parser, KnowledgeSource.status)
        .where(KnowledgeSource.raw_path.ilike("%.fb2"))).all():
    chunks = session.execute(
        select(func.count(), func.sum(func.length(KnowledgeChunk.text)),
               func.max(func.length(KnowledgeChunk.text)))
        .where(KnowledgeChunk.source_id == source_id)).one()
    print(f"  {filename or 'без имени'} | parser={parser} | {status}")
    print(f"    фрагментов={chunks[0]} | символов={chunks[1] or 0} | самый длинный={chunks[2] or 0}")
    for text in session.scalars(
            select(KnowledgeChunk.text).where(KnowledgeChunk.source_id == source_id)
            .order_by(KnowledgeChunk.ordinal).limit(3)).all():
        print(f"    фрагмент: {text[:150]!r}")
PYEOF
