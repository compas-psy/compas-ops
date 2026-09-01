#!/bin/bash
# Проверка ADR-020 (strict zero-paid Knowledge lock): действительно ли
# вопросы про холестерин/врачей (со скриншотов, интерфейс которых не
# похож на HELM вообще — нет доменного меню, локальный `find`, "Searching
# past sessions") хоть раз прошли через РЕАЛЬНЫЙ Probe этого сервера, и
# если да — не эскалировались ли платно.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

echo '=== точное совпадение по query_hash обоих вопросов ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select query_hash, domain, mode, paid_ai_used, created_at
   from knowledge_answer_runs
   where query_hash in (
     'dad4489d1ccd559a7b196ecb31c41ca727b3eaaf49459918700e2e8c018e18d9',
     '4fbccfbf8658783bbdba033024e9f3e2a2d2c384cc63c9c18b89784201f95b8d'
   )"

echo '=== всё, что прошло через Probe за последний час (любой вопрос) ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select query_hash, domain, mode, paid_ai_used, created_at
   from knowledge_answer_runs
   where created_at > now() - interval '2 hours'
   order by created_at desc"

echo '=== для сравнения: внешние вызовы litellm (платные модели) за это же время ==='
cd /opt/helm/compose && sudo docker compose logs litellm --since 2h 2>&1 | tail -30 || true
