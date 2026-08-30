#!/usr/bin/env bash
# Диагностика: почему на резенд файла в Telegram проскочило английское
# "Couldn't download your attachment ... (TimedOut)" — не наш текст (у нас
# только русский, без имени файла, без "(TimedOut)"). Похоже на отдельный
# от нашего pre_gateway_dispatch-гейта механизм. Read-only.
set -euo pipefail

echo "=== LOG вокруг 11:29-11:33 (московское время сервера) ==="
sudo journalctl -u hermes-gateway --since "11:29:00" --until "11:33:30" --no-pager | tail -200

echo
echo "=== ИСТОЧНИК СТРОКИ В ИСХОДНИКЕ HERMES ==="
grep -rn "Couldn't download your attachment" /home/helm/.hermes/hermes-agent/ 2>/dev/null || echo "не найдено в hermes-agent"

echo
echo "=== TimedOut — где ещё упоминается ==="
grep -rln "TimedOut" /home/helm/.hermes/hermes-agent/ --include=*.py 2>/dev/null | head -20
