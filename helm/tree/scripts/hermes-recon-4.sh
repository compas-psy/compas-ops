#!/bin/bash
# Четвёртая волна разведки для max-bridge (ADR-020). Read-only.
# Гипотеза: у Hermes уже есть встроенный "API server" platform —
# stateless request/response, ровно то, что нужно MAX (Control Plane
# как клиент существующего HTTP-входа, вместо новой платформы).
# Запуск: bash /tmp/hermes-recon-4.sh
set -uo pipefail
cd /home/helm/.hermes/hermes-agent || exit 1

echo '=== 1. заголовок и докстрока файла ==='
sed -n '1,60p' gateway/platforms/api_server.py

echo
echo '=== 2. классы и маршруты (структура файла) ==='
grep -n '^class \|@.*route\|async def connect\|async def send\|async def disconnect\|add_routes\|web.post\|web.Application' gateway/platforms/api_server.py | head -60

echo
echo '=== 3. конфиг API_SERVER: порт/хост/auth по умолчанию ==='
grep -n 'API_SERVER\|api_server' gateway/config.py | head -30

echo
echo '=== 4. включён ли api_server прямо сейчас на этом сервере ==='
grep -rn 'api_server\|API_SERVER' ~/.hermes/config.yaml 2>&1 | head -20
ss -tlnp 2>/dev/null | grep -v '5432\|8080\|48869\|18080' || echo "(портов сверх известных не найдено)"
