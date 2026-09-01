#!/bin/bash
# Разовая read-only разведка: какие именно файлы последнего ZIP-батча
# упали и почему — bot-уведомление (§14.5.2) даёт только агрегат
# ("8 ошибка"), не список. Смотрим knowledge_batch_items напрямую.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

echo '=== последние 3 батча ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, archive_filename, status, created_at
   from knowledge_ingest_batches order by created_at desc limit 3"

echo '=== упавшие элементы батчей со статусом completed_with_errors ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -x -c \
  "select b.archive_filename, i.archive_member_path_original,
          i.status, i.error_code, i.error_detail_redacted
   from knowledge_batch_items i
   join knowledge_ingest_batches b on b.id = i.batch_id
   where b.status = 'completed_with_errors'
     and i.status not in ('ready', 'exact_duplicate')
   order by b.created_at desc, i.ordinal"
