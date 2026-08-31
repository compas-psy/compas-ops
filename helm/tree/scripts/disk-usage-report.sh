#!/bin/bash
# Read-only разведка места на диске (запрос владельца 31.08.2026: "диск
# наполовину заполнен, проанализируй и избавь от мусора" — сначала смотрим,
# что там реально лежит, ничего не удаляем в этом скрипте).
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

echo '=== верхний уровень / (без /proc, /sys) ==='
sudo du -xh --max-depth=1 / 2>/dev/null | sort -rh

echo
echo '=== /var — по подкаталогам ==='
sudo du -xh --max-depth=2 /var 2>/dev/null | sort -rh | head -30

echo
echo '=== docker system df -v ==='
sudo docker system df -v 2>/dev/null | head -80

echo
echo '=== apt-кэш ==='
sudo du -sh /var/cache/apt 2>/dev/null

echo
echo '=== journal (systemd) ==='
sudo journalctl --disk-usage 2>/dev/null

echo
echo '=== установленные ядра linux-image ==='
dpkg -l 2>/dev/null | grep linux-image || true
echo "текущее загруженное: $(uname -r)"

echo
echo '=== /opt — по подкаталогам ==='
sudo du -xh --max-depth=2 /opt 2>/dev/null | sort -rh | head -30
