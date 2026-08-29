#!/bin/bash
# Диагностика (read-only): владелец отправил файл в MAX, получил меню
# доменов, ответил "Health" — и 2+ минуты ничего не пришло в ответ.
# Смотрим ВСЁ за один заход: логи helm-core, состояние pending/source/
# outbox в Postgres — не гадаем, что именно застряло.
set -uo pipefail

echo "===== 1. Логи helm-core, последние 60 строк ====="
cd /opt/helm/compose
sudo docker compose logs helm-core --tail 60

echo
echo "===== 2. knowledge_pending_attachments (ожидаем 0 строк, если резолв прошёл) ====="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, channel, original_filename, created_at from knowledge_pending_attachments order by created_at desc limit 5"

echo
echo "===== 3. knowledge_sources — последние 5 (ожидаем новую с domain=health) ====="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, domain, original_filename, sensitivity, status, created_at from knowledge_sources order by created_at desc limit 5"

echo
echo "===== 4. knowledge_ingest_jobs — последние 5 ====="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, source_id, channel, status, error, created_at from knowledge_ingest_jobs order by created_at desc limit 5"

echo
echo "===== 5. outbox — последние 5 (статус доставки подтверждения) ====="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, channel, recipient, status, attempts, next_attempt_at, payload_reference from outbox order by next_attempt_at desc limit 5"

echo
echo "===== 6. channel_events — последние 5 (дедуп повторной доставки вебхука) ====="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select channel, external_message_id, owner_id, received_at from channel_events order by received_at desc limit 5"

echo "DONE"
