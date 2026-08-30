#!/usr/bin/env bash
# Ускоряет естественный ретрай доставщика (dispatch.py) для зависшего
# PENDING-сообщения в outbox — сбрасывает next_attempt_at на "сейчас",
# ничего не меняет в бизнес-логике, тот же цикл (_dispatch_loop, 5с) сам
# подхватит его на следующем тике. Read+один точечный UPDATE.
set -euo pipefail

echo "=== ДО ==="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, channel, recipient, status, attempts, next_attempt_at
   from outbox where channel = 'telegram' and status = 'PENDING'
   order by next_attempt_at desc;"

sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "update outbox set next_attempt_at = now()
   where channel = 'telegram' and status = 'PENDING';"

echo
echo "=== Жду один тик дистпетчера (7с) ==="
sleep 7

echo
echo "=== ПОСЛЕ ==="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, channel, recipient, status, attempts, next_attempt_at
   from outbox where channel = 'telegram'
   order by next_attempt_at desc limit 3;"

echo
echo "=== helm-core: строка dispatch за последние 20с ==="
sudo docker compose -f /opt/helm/compose/docker-compose.yml logs helm-core --since 20s | grep -i dispatch || echo "нет строк dispatch (хороший знак, если status теперь SENT)"
