#!/usr/bin/env bash
set -euo pipefail

sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "update outbox set status = 'PENDING', attempts = 0, next_attempt_at = now()
   where channel = 'telegram' and status = 'FAILED'
   and id = '62378b9a-f6da-4b25-906c-471a32ce65ab';"

sleep 7

echo "=== Статус после тика ==="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, status, attempts from outbox where id = '62378b9a-f6da-4b25-906c-471a32ce65ab';"
