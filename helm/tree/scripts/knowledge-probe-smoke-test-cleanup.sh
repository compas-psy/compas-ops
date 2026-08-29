#!/bin/bash
# Убрать тестовую запись knowledge-probe-smoke-test.sh (P8.5) из живой
# базы знаний после подтверждённого живого теста — иначе она может
# однажды всплыть как "источник" в ответе на реальный инженерный вопрос
# (совпадение по стеммам вроде "деплой"/"проверка").
#
# Заодно обнуляет knowledge_answer_runs: на момент этого скрипта
# КАЖДАЯ строка там — от смоук-теста или живой проверки деплоя P8.5, не
# от реального использования владельцем. TRUNCATE, а не выборочное
# удаление по query_hash: пересчитывать hashlib.sha256(casefold(...))
# средствами SQL (pgcrypto digest(), lower() — не то же самое, что
# Python casefold()) — усложнение без пользы, когда весь стол и так
# тестовый мусор на эту минуту.
set -euo pipefail

echo "== до удаления =="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, original_filename, domain from knowledge_sources where original_filename = 'deploy-smoke-test.md'"

sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "delete from knowledge_chunks where source_id in (select id from knowledge_sources where original_filename = 'deploy-smoke-test.md')"

sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "delete from knowledge_sources where original_filename = 'deploy-smoke-test.md'"

sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "truncate knowledge_answer_runs"

echo
echo "== после удаления (обе пустые) =="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select count(*) from knowledge_sources where original_filename = 'deploy-smoke-test.md'"
sudo docker exec helm-postgres-1 psql -U helm -d helm -c "select count(*) from knowledge_chunks"
sudo docker exec helm-postgres-1 psql -U helm -d helm -c "select count(*) from knowledge_answer_runs"

echo "DONE"
