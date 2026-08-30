#!/bin/bash
# Продолжение recon-2: adapter.py явно скачивает вложения (get_file() +
# download_as_bytearray(), реальный python-telegram-bot), но это его
# ВНУТРЕННИЙ механизм для agentic-чтения чифом (тот самый, что мы уже
# видели живьём на "12_fishek.pdf"). Вопрос: доходит ли что-то из этого
# до event, который получает pre_gateway_dispatch(event, gateway) в
# helm-control? Смотрим _build_message_event (строит event) и
# MessageEvent/MessageType (структура). Read-only.
set -uo pipefail

ADAPTER=/home/helm/.hermes/hermes-agent/plugins/platforms/telegram/adapter.py
HERMES_SRC=/home/helm/.hermes/hermes-agent

echo "===== _build_message_event (конструирует event, отдаваемый плагинам) ====="
sed -n '10543,10713p' "$ADAPTER"

echo
echo "===== Где определены классы MessageEvent / MessageType / Message (импорты adapter.py) ====="
grep -n "^from \|^import " "$ADAPTER" | grep -i "message\|event\|models\|types"

echo
echo "===== class MessageEvent / class MessageType — сам файл определения ====="
DEF_FILE=$(grep -rl "^class MessageEvent" "$HERMES_SRC" 2>/dev/null | grep -v __pycache__ | head -1)
echo "DEF_FILE=${DEF_FILE:-НЕ НАЙДЕНО}"
if [ -n "$DEF_FILE" ]; then
  grep -n "^class MessageEvent" -A 60 "$DEF_FILE"
fi

echo
echo "===== class MessageType — сам файл определения ====="
TYPE_FILE=$(grep -rl "^class MessageType" "$HERMES_SRC" 2>/dev/null | grep -v __pycache__ | head -1)
echo "TYPE_FILE=${TYPE_FILE:-НЕ НАЙДЕНО}"
if [ -n "$TYPE_FILE" ]; then
  grep -n "^class MessageType" -A 30 "$TYPE_FILE"
fi

echo "DONE"
