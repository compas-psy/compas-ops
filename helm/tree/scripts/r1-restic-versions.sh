#!/bin/bash
# HELM v4.0 RESCUE · R1: какие рычаги вообще есть у restic и rclone на
# этом сервере. Read-only, секунды.
#
# Нужно потому, что три подряд правки настроек не помогли, и прежде чем
# пробовать четвёртую, надо знать, поддерживается ли она этой версией.
# Подбирать флаги, которых в бинаре нет, — не отладка, а гадание.
set -uo pipefail

echo "############ ВЕРСИИ ############"
restic version 2>&1 | head -2
rclone version 2>&1 | head -3

echo
echo "############ ЕСТЬ ЛИ --pack-size ############"
# Появился в restic 0.14. Если его нет, единственный способ уменьшить
# размер отдельной выгрузки — сменить хранилище.
restic backup --help 2>&1 | grep -E 'pack-size|read-concurrency' || echo "  нет ни того, ни другого"

echo
echo "############ РАЗМЕР ПАЧЕК В РЕПОЗИТОРИИ ############"
# Косвенно показывает, какого размера PUT-запросы уходят на Яндекс.
sudo timeout 240 env \
  RESTIC_REPOSITORY="rclone:yandex:helm-backup" \
  RESTIC_PASSWORD_FILE="/etc/helm/secrets/restic_password" \
  RCLONE_TIMEOUT=2m RCLONE_CONTIMEOUT=1m \
  restic stats --mode raw-data 2>&1 | tail -8

echo
echo "############ ГОТОВО ############"
