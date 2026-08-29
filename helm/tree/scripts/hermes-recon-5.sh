#!/bin/bash
# Пятая волна разведки для MAX (ADR-020 пересматривается). Read-only.
# Найден встроенный OpenAI-совместимый API server у Hermes (POST
# /v1/responses с X-Hermes-Session-Key = named conversation из §10.2) —
# нужен точный контракт вызова, вместо самодельной платформы/адаптера.
# Запуск: bash /tmp/hermes-recon-5.sh
set -uo pipefail
cd /home/helm/.hermes/hermes-agent || exit 1

echo '=== 1. валидация API_SERVER_KEY (минимальная сила) ==='
grep -n '_has_usable_api_server_key' -A 25 gateway/config.py | head -40

echo
echo '=== 2. откуда Hermes читает env (.env путь) ==='
grep -n 'load_hermes_dotenv\|\.hermes/\.env\|API_SERVER_KEY\s*=' hermes_cli/*.py gateway/*.py 2>/dev/null | grep -i 'dotenv\|\.env' | head -10

echo
echo '=== 3. маршрут POST /v1/responses — сигнатура и тело запроса ==='
grep -n "'/v1/responses'\|\"v1/responses\"\|def.*responses" gateway/platforms/api_server.py | head -20

echo
echo '=== 4. X-Hermes-Session-Key — как связывает вызовы в один разговор ==='
grep -n 'X-Hermes-Session-Key\|x_hermes_session_key\|session_key' gateway/platforms/api_server.py | head -30

echo
echo '=== 5. POST /v1/runs — форма запроса/ответа, если решим на неё опираться ==='
grep -n "'/v1/runs'\|\"v1/runs\"" gateway/platforms/api_server.py | head -10

echo
echo '=== 6. дефолтные HOST/PORT ==='
grep -n 'API_SERVER_HOST\|API_SERVER_PORT\|8642' gateway/platforms/api_server.py gateway/config.py | head -20
