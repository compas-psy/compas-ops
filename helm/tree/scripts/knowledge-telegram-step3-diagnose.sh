#!/usr/bin/env bash
# Диагностика: почему не пришло 3-е сообщение (уведомление о завершении
# разбора) для последнего Telegram-вложения. Read-only.
set -euo pipefail

echo "=== Последние knowledge_ingest_jobs ==="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select j.id, j.status, j.channel, j.recipient, j.created_at, s.original_filename, s.domain
   from knowledge_ingest_jobs j join knowledge_sources s on s.id = j.source_id
   order by j.created_at desc limit 5;"

echo
echo "=== Последние outbox ==="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, channel, recipient, status, attempts, next_attempt_at
   from outbox order by next_attempt_at desc limit 10;"

echo
echo "=== helm-knowledge-worker: контейнер жив? ==="
sudo docker compose -f /opt/helm/compose/docker-compose.yml ps helm-knowledge-worker

echo
echo "=== helm-knowledge-worker: последние 60 строк лога ==="
sudo docker compose -f /opt/helm/compose/docker-compose.yml logs helm-knowledge-worker --tail 60

echo
echo "=== helm-core: последние 30 строк лога (доставка outbox) ==="
sudo docker compose -f /opt/helm/compose/docker-compose.yml logs helm-core --tail 30
