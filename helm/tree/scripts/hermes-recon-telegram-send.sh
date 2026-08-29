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

# systemctl status hermes-gateway показывает реальный интерпретатор:
# /home/helm/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main —
# hermes_cli живёт в venv, не в системном python3 (первый прогон этого
# скрипта молча дал пустой import и упал на find-эвристике, зацепив
# постороннюю папку "gateway" внутри hermes-agent/tests/).
HERMES_PY=/home/helm/.hermes/hermes-agent/venv/bin/python3
if [ ! -x "$HERMES_PY" ]; then
  HERMES_PY=$(command -v python3)
fi
echo "HERMES_PY=$HERMES_PY"

HERMES_SRC=$("$HERMES_PY" -c "import hermes_cli, os; print(os.path.dirname(os.path.dirname(hermes_cli.__file__)))" 2>/dev/null)
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
