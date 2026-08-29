#!/bin/bash
# Продолжение разведки: platforms/telegram — директория, не файл. Ищем
# реальный класс адаптера и сигнатуру send() внутри неё, плюс тип поля
# SessionSource.platform (gateway/session.py:149) и абстрактную
# сигнатуру BasePlatformAdapter.send для сравнения.
set -uo pipefail

HERMES_SRC=/home/helm/.hermes/hermes-agent
TG_DIR="$HERMES_SRC/plugins/platforms/telegram"

echo "===== Содержимое $TG_DIR ====="
find "$TG_DIR" -maxdepth 1 -iname "*.py" | grep -v __pycache__

echo
echo "===== class .*Adapter в директории telegram ====="
grep -rn "class .*Adapter" "$TG_DIR"/*.py 2>/dev/null

echo
echo "===== def send( в директории telegram ====="
grep -rln "def send(" "$TG_DIR"/*.py 2>/dev/null

for f in $(grep -rln "def send(" "$TG_DIR"/*.py 2>/dev/null); do
  echo
  echo "-- $f --"
  awk '/def send\(/{p=1} p{print} p && /^    def [a-zA-Z_]+\(/ && !/def send\(/{exit}' "$f" | head -60
done

echo
echo "===== BasePlatformAdapter.send — абстрактная сигнатура ====="
grep -rln "class BasePlatformAdapter" "$HERMES_SRC"/gateway/*.py "$HERMES_SRC"/plugins/*.py 2>/dev/null
BASE_FILE=$(grep -rln "class BasePlatformAdapter" "$HERMES_SRC"/gateway/*.py "$HERMES_SRC"/plugins/*.py 2>/dev/null | head -1)
if [ -n "$BASE_FILE" ]; then
  echo "BASE_FILE=$BASE_FILE"
  awk '/class BasePlatformAdapter/,0' "$BASE_FILE" | grep -n "def send\|def " | head -20
fi

echo
echo "===== SessionSource.platform (gateway/session.py около строки 149) ====="
sed -n '140,175p' "$HERMES_SRC/gateway/session.py"

echo "DONE"
