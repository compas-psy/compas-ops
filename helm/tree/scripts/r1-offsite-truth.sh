#!/bin/bash
# HELM v4.0 RESCUE · R1: настоящее состояние offsite-репозитория. Read-only.
#
# Зачем отдельный прогон. До 02.09.2026 состояние точки возврата читалось
# командой `stat /var/lib/helm-guardian/last-backup` БЕЗ sudo. Каталог
# роли helm не читается, и проверка честно отвечала «отметки нет» на
# существующую отметку — то есть отчёт «бэкапа нет» был дефектом
# проверки, а не фактом. Здесь всё читается от root, а список снапшотов
# берётся у самого restic, а не пересказывается.
set -uo pipefail

echo "############ 1. ОТМЕТКИ ############"
for m in last-backup last-restore-test last-local-checkpoint; do
  sudo stat -c "  $m: %y" "/var/lib/helm-guardian/$m" 2>/dev/null || echo "  $m: отметки нет"
done

echo
echo "############ 2. ТАЙМЕР И ПОСЛЕДНИЕ ПРОГОНЫ ############"
systemctl list-timers 'helm-backup*' --all --no-pager 2>/dev/null | head -5
echo "--- последние завершения helm-backup.service ---"
sudo journalctl -u helm-backup.service --since '4 days ago' --no-pager 2>/dev/null \
  | grep -E 'Starting|Finished|Failed|BACKUP DONE|Fatal|error' | tail -25 \
  || echo "  журнал недоступен"

echo
echo "############ 3. ЧТО РЕАЛЬНО ЛЕЖИТ В РЕПОЗИТОРИИ ############"
# Список снапшотов — это только метаданные, не выгрузка данных: дёшево
# даже на нестабильном WebDAV. timeout, чтобы прогон не завис на нём.
sudo timeout 300 env \
  RESTIC_REPOSITORY="rclone:yandex:helm-backup" \
  RESTIC_PASSWORD_FILE="/etc/helm/secrets/restic_password" \
  RCLONE_TIMEOUT=2m RCLONE_CONTIMEOUT=1m RCLONE_LOW_LEVEL_RETRIES=5 \
  restic snapshots --compact 2>&1 | tail -40
echo "код возврата restic: $?"

echo
echo "############ ГОТОВО ############"
