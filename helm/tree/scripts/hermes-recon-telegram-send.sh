#!/bin/bash
# Разведка (read-only): почему gateway.adapters[source.platform].send(...)
# в helm-control молча ничего не доставляет. Не гадаем по названию — читаем
# реальный класс адаптера Telegram и то, как populated gateway.adapters.
set -euo pipefail

HERMES_SRC=$(python3 -c "import hermes_cli, os; print(os.path.dirname(os.path.dirname(hermes_cli.__file__)))" 2>/dev/null || true)
if [ -z "$HERMES_SRC" ]; then
  HERMES_SRC=$(find / -maxdepth 6 -type d -name "gateway" -path "*hermes*" 2>/dev/null | head -1 | xargs dirname)
fi
echo "HERMES_SRC=$HERMES_SRC"
echo

echo "===== Файлы platforms/ ====="
find "$HERMES_SRC" -path "*platforms*" -iname "*.py" 2>/dev/null | grep -i telegram

echo
echo "===== class ...Adapter для Telegram — сигнатура send() ====="
TG_FILE=$(find "$HERMES_SRC" -path "*platforms*" -iname "*telegram*" 2>/dev/null | grep -v __pycache__ | head -1)
echo "TG_FILE=$TG_FILE"
grep -n "class \|def send" "$TG_FILE"
echo
echo "-- полное тело send() --"
awk '/def send\(/,/^    def [a-z_]+\(/' "$TG_FILE" | head -60

echo
echo "===== self.adapters — где заполняется, какой ключ ====="
grep -rn "self.adapters\[" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -20
grep -rn "adapters\s*=\s*{" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -10
grep -rn "adapters:\s*dict\|adapters:\s*Dict" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -10

echo
echo "===== source.platform — тип поля (enum? str?) ====="
grep -rn "class .*Source\b" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -5
grep -rn "platform:" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -10

echo "DONE"
