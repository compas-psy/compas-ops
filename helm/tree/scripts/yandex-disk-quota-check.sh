#!/bin/bash
# Read-only: живой прогон action=deploy упал не на SSH (это уже
# исправлено keepalive-фиксом — шаг реально проработал 15 минут вместо
# обрыва на 4.5), а на самом Yandex Disk WebDAV: rclone/restic получали
# "500 Internal Server Error" и "i/o timeout" на одни и те же 3 чанка
# три раза подряд с интервалом ~5 минут, пока restic не сдался. Самая
# частая причина именно такой картины — исчерпана квота места на Яндекс
# Диске. Проверяем это напрямую, ничего не заливаем и не трогаем.
set -uo pipefail

echo '=== квота и занятое место на Яндекс Диске (rclone about) ==='
sudo bash -c '
  export RESTIC_REPOSITORY="rclone:yandex:helm-backup"
  rclone about yandex: 2>&1
'

echo '=== размер репозитория бэкапов на Яндекс Диске ==='
sudo bash -c '
  rclone size yandex:helm-backup 2>&1
'
