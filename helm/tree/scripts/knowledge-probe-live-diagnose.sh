#!/bin/bash
# Разовая диагностика "оба канала молчат" после реального сообщения с
# тестовым запросом Knowledge Probe (P8.5). Читает ВСЁ нужное за один
# заход — логи helm-core и hermes-gateway, последние задачи/события/
# исходящие ПО ВРЕМЕНИ (не по id — id это uuid.uuid4() без хронологии,
# см. F-260829-24), а не строит гипотезу по одному источнику.
set -euo pipefail

echo "===== helm-core: логи за 15 минут ====="
cd /opt/helm/compose
sudo docker compose logs helm-core --since 15m 2>&1 | tail -150

echo
echo "===== hermes-gateway: логи за 15 минут ====="
sudo journalctl -u hermes-gateway --since "15 min ago" --no-pager | tail -150

echo
echo "===== Последние 10 задач (по created_at) ====="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, origin_channel, origin_owner_id, status, created_at from tasks order by created_at desc limit 10"

echo
echo "===== Последние 15 событий задач (по timestamp) ====="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select task_id, actor, event_type, timestamp from task_events order by timestamp desc limit 15"

echo
echo "===== Последние 10 исходящих (по next_attempt_at) ====="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, channel, recipient, status, attempts, next_attempt_at from outbox order by next_attempt_at desc limit 10"

echo
echo "===== Последние 5 answer_runs (по created_at) ====="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select query_hash, mode, paid_ai_used, evidence_count, created_at from knowledge_answer_runs order by created_at desc limit 5"

echo "DONE"
