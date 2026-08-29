#!/bin/bash
# Дневной бюджетный потолок на каждый LiteLLM virtual key профиля Hermes —
# чтобы баг/зацикливание не могли сжечь бюджет бесконтрольно (владелец,
# 29.08.2026, после проверки реального spend перед Milestone B).
set -euo pipefail

MASTER_KEY=$(sudo cat /etc/helm/secrets/litellm_master_key)
DAILY_BUDGET="${1:-5}"

for profile in default business engineering health reviewer; do
  key=$(sudo cat "/etc/helm/secrets/hermes_${profile}_litellm_key")
  response=$(curl -s -X POST http://127.0.0.1:4000/key/update \
    -H "Authorization: Bearer ${MASTER_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"${key}\", \"max_budget\": ${DAILY_BUDGET}, \"budget_duration\": \"24h\"}")
  # Ответ echo-ит сам ключ обратно — не печатаем сырое тело, только
  # безопасные поля (значение секрета никогда не должно попасть в чат).
  echo "${profile}:"
  echo "${response}" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print('  max_budget:', d.get('max_budget'))
    print('  budget_duration:', d.get('budget_duration'))
    print('  key_alias:', d.get('key_alias'))
except Exception as e:
    print('  ПАРСИНГ НЕ УДАЛСЯ (ошибка API?):', type(e).__name__)
"
done
