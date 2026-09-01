#!/bin/bash
# Read-only разведка перед повторным запуском backup.sh: рабочая гипотеза
# зависания — не сам restic завис, а SSH-соединение оборвалось по
# idle-таймауту, пока backup.sh молча работал (deploy.yml теперь шлёт
# ServerAlive keepalive, отдельный фикс). Но раз прошлый restic-процесс
# убило разрывом SSH, а не штатным завершением — сначала проверяем, не
# остался ли он живым процессом на сервере и не держит ли репозиторий
# залоченным (первый следующий backup.sh иначе сразу упадёт на "repository
# is already locked"). Ничего не трогаем, не снимаем локи, не убиваем
# процессы — только смотрим.
set -uo pipefail

echo '=== жив ли ещё restic-процесс от прошлой попытки ==='
ps aux | grep -E '[r]estic|[b]ackup\.sh' || echo "процессов нет — прошлая попытка не висит фоном"

echo '=== текущие локи репозитория (read-only, ничего не меняет) ==='
sudo bash -c '
  export RESTIC_REPOSITORY="rclone:yandex:helm-backup"
  export RESTIC_PASSWORD_FILE=/etc/helm/secrets/restic_password
  restic list locks 2>&1
'

echo '=== снапшоты в репозитории (был ли предыдущий забег дописан хоть частично) ==='
sudo bash -c '
  export RESTIC_REPOSITORY="rclone:yandex:helm-backup"
  export RESTIC_PASSWORD_FILE=/etc/helm/secrets/restic_password
  restic snapshots 2>&1
'

echo '=== сколько данных реально предстоит прочитать (то, что задаёт время первого бэкапа) ==='
FORGEJO_DATA=$(sudo docker volume inspect -f '{{.Mountpoint}}' helm_forgejo_data 2>/dev/null)
du -sh "$FORGEJO_DATA" 2>/dev/null || echo "Forgejo volume: не удалось померить"
du -sh /opt/helm-knowledge 2>/dev/null || echo "/opt/helm-knowledge: не удалось померить"
du -sh /opt/helm/n8n/exports /opt/helm/config /opt/helm/guardian 2>/dev/null

echo '=== сеть: скорость/доступность rclone:yandex прямо сейчас ==='
sudo bash -c '
  export RESTIC_REPOSITORY="rclone:yandex:helm-backup"
  export RESTIC_PASSWORD_FILE=/etc/helm/secrets/restic_password
  timeout 20 restic cat config 2>&1 || echo "не ответил за 20 секунд"
'
