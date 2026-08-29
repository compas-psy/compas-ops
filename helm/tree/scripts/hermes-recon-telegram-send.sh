#!/bin/bash
# Разведка (read-only): почему gateway.adapters[source.platform].send(...)
# в helm-control молча ничего не доставляет. Не гадаем по названию — читаем
# реальный класс адаптера Telegram и то, как populated gateway.adapters.
#
# НАЙДЕНО при первом запуске этого же скрипта: `set -e` + `pipefail` +
# `xargs dirname` без входных данных ("dirname: missing operand", код 1)
# убивали скрипт ДО первого echo — пустой вывод целиком, без единой
# строки диагностики. Переписано без xargs на пустом вводе.
set -uo pipefail

HERMES_SRC=""
CAND=$(python3 -c "import hermes_cli, os; print(os.path.dirname(os.path.dirname(hermes_cli.__file__)))" 2>/dev/null)
if [ -n "$CAND" ]; then
  HERMES_SRC="$CAND"
else
  GW_DIR=$(find / -maxdepth 8 -type d -name "gateway" -path "*hermes*" 2>/dev/null | head -1)
  if [ -n "$GW_DIR" ]; then
    HERMES_SRC=$(dirname "$GW_DIR")
  fi
fi
echo "HERMES_SRC=${HERMES_SRC:-НЕ НАЙДЕНО}"

if [ -z "$HERMES_SRC" ]; then
  echo "не нашли исходники Hermes автоматически — venv по другому пути?"
  echo "проверка вручную: find / -maxdepth 8 -iname 'hermes_cli' -type d 2>/dev/null"
  exit 0
fi

echo
echo "===== Файлы platforms/*telegram* ====="
find "$HERMES_SRC" -path "*platforms*" -iname "*telegram*" 2>/dev/null | grep -v __pycache__

TG_FILE=$(find "$HERMES_SRC" -path "*platforms*" -iname "*telegram*" 2>/dev/null | grep -v __pycache__ | head -1)
if [ -z "$TG_FILE" ]; then
  echo "файл адаптера Telegram не найден по пути *platforms*telegram*"
  exit 0
fi
echo "TG_FILE=$TG_FILE"

echo
echo "===== class / def send() в $TG_FILE ====="
grep -n "class \|def send" "$TG_FILE"

echo
echo "-- тело send(), первые 60 строк --"
awk '/def send\(/,/^    def [a-zA-Z_]+\(/' "$TG_FILE" | head -60

echo
echo "===== self.adapters — где заполняется, каким ключом ====="
grep -rn "self.adapters\[" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -20
grep -rn "adapters\s*=\s*{" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -10
grep -rn "adapters:\s*[Dd]ict" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -10

echo
echo "===== source.platform — тип поля ====="
grep -rn "class .*Source\b" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -5
grep -rn "platform:" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -10

echo "DONE"
