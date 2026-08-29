#!/bin/bash
# Разовая разведка внутренностей Hermes gateway для плагина max-bridge
# (ADR-020). Read-only, ничего не меняет. Запускается на сервере:
#   bash /tmp/hermes-recon.sh
set -uo pipefail
cd /home/helm/.hermes/hermes-agent || exit 1

echo '=== 2. SessionSource (по всему дереву) ==='
grep -rn 'class SessionSource' --include=*.py .

echo
echo '=== 2b. содержимое SessionSource ==='
F=$(grep -rln 'class SessionSource' --include=*.py . | head -1)
if [ -n "$F" ]; then
  sed -n "/class SessionSource/,/^class /p" "$F" | head -60
else
  echo "класс SessionSource не найден по имени — искать иначе"
fi

echo
echo '=== 3. метод, оборачивающий pre_gateway_dispatch ==='
awk 'NR<=17205 && /async def /{last=$0; lastline=NR} END{print lastline": "last}' gateway/run.py
sed -n '17150,17205p' gateway/run.py

echo
echo '=== 4. Platform._missing_ целиком ==='
sed -n '350,410p' gateway/config.py

echo
echo '=== 5. BasePlatformAdapter.send абстрактный ==='
grep -n 'abstractmethod' -A3 gateway/platforms/base.py | grep -B1 -A3 'def send'
