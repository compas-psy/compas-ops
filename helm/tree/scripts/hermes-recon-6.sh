#!/bin/bash
# Шестая, финальная волна разведки для MAX/§10.2. Read-only.
# Нужен точный контракт POST /v1/responses и способ авторизации —
# последнее перед реализацией, чтобы не гадать, как с chat_id у MAX.
# Запуск: bash /tmp/hermes-recon-6.sh
set -uo pipefail
cd /home/helm/.hermes/hermes-agent || exit 1

echo '=== 1. _handle_responses целиком (начало) ==='
sed -n '6204,6420p' gateway/platforms/api_server.py

echo
echo '=== 2. как проверяется Authorization / API-ключ ==='
grep -n 'def _check_auth\|def _require_auth\|Authorization\|Bearer\|api_key ==' gateway/platforms/api_server.py | head -30

echo
echo '=== 3. DEFAULT_HOST ==='
grep -n 'DEFAULT_HOST' gateway/platforms/api_server.py | head -5
