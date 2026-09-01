#!/bin/bash
# Разовое: 8 элементов батча "Анализы и обследования.zip" упали на
# check_queue_depth() (лимит 50 задач в очереди на тенанта, §14.4) —
# временный затор, не порча данных. retry_failed() — штатная функция
# продукта (тот же путь, что уже вызывает internal API POST /knowledge/
# batches/{id}/retry-failed), не деплой и не изменение схемы — обычная
# операция повторной постановки в очередь, доступная любому вызывающему
# коду сегодня.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail
cd /opt/helm/compose || exit 1

BATCH_ID="d904fe0a-8abf-44da-b778-229fb91d7a47"

echo '=== текущая глубина очереди по тенанту этого батча ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select count(*) from knowledge_ingest_jobs j
   join knowledge_ingest_batches b on b.knowledge_user_id = j.knowledge_user_id
   where b.id = '$BATCH_ID' and j.status in ('pending','running')"

echo '=== retry_failed() ==='
sudo docker compose exec -T helm-core python3 <<PY
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from helm_core.config import get_settings
from helm_core.knowledge.batch_intake import retry_failed

engine = create_engine(get_settings().database_url)
with Session(engine) as s:
    batch = retry_failed(s, uuid.UUID("$BATCH_ID"))
    s.commit()
    print("batch status после retry:", batch.status if batch else "не найден")
PY

echo '=== статусы элементов батча после retry ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select archive_member_path_original, status, error_code, retryable
   from knowledge_batch_items where batch_id = '$BATCH_ID'
   order by ordinal"
