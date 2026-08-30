#!/bin/bash
# Продолжение knowledge-telegram-attachment-recon.sh: тот скрипт нашёл
# верный каталог (plugins/platforms/telegram), но TG_FILE там резолвился
# в саму ДИРЕКТОРИЮ (find вернул её раньше файлов внутри), поэтому grep
# по методам адаптера ничего не показал. Читаем файлы внутри каталога
# напрямую. Read-only, ничего не меняет.
set -uo pipefail

TG_DIR=/home/helm/.hermes/hermes-agent/plugins/platforms/telegram
echo "===== Файлы в $TG_DIR ====="
find "$TG_DIR" -type f -iname "*.py" | grep -v __pycache__

echo
echo "===== class / def в каждом файле ====="
find "$TG_DIR" -type f -iname "*.py" | grep -v __pycache__ | while read -r f; do
  echo "-- $f --"
  grep -n "^class \|    def " "$f"
done

echo
echo "===== get_file/download/bot_token/self.bot/Bot( во всех файлах ====="
grep -rn "get_file\|download\|bot_token\|self\.token\|self\.bot\b\|Bot(" "$TG_DIR" --include="*.py" | grep -v __pycache__

echo
echo "===== Event/InboundMessage — где определён класс, что за поля (gateway/) ====="
HERMES_SRC=/home/helm/.hermes/hermes-agent
grep -rln "class InboundMessage\|class Event\b\|@dataclass" "$HERMES_SRC"/gateway/*.py 2>/dev/null | grep -v __pycache__

echo
echo "-- InboundMessage/Event полное определение, если нашлось --"
EVENT_FILE=$(grep -rl "class InboundMessage" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -1)
if [ -z "$EVENT_FILE" ]; then
  EVENT_FILE=$(grep -rl "^class Event" "$HERMES_SRC"/gateway/*.py 2>/dev/null | head -1)
fi
echo "EVENT_FILE=${EVENT_FILE:-НЕ НАЙДЕНО}"
if [ -n "$EVENT_FILE" ]; then
  awk '/^class (InboundMessage|Event)\b/,0' "$EVENT_FILE" | head -60
fi

echo "DONE"
