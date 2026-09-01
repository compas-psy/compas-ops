#!/bin/bash
# Ежедневный бэкап (§26.1): PostgreSQL (все БД), Control Plane config,
# LiteLLM config, профили/state/kanban Hermes, данные Forgejo (§18.7) и
# выгрузка workflow n8n (§17.5) — в restic-репозиторий на Яндекс Диске.
# Запускается от root (helm-backup.service).
#
# Оговорка про репозитории Forgejo: копируются на живом сервисе, без
# остановки. Объекты git неизменяемы, поэтому риск ограничен одним
# случаем — push ровно в момент копирования, когда ref уже обновлён, а
# объект ещё не скопирован. Такой репозиторий восстанавливается из
# GitHub-зеркала (§18.4) или из снапшота следующего дня; останавливать
# Forgejo ради этого окна дороже, чем оно стоит.
#
# Не бэкапится: сам установленный Hermes (~/.hermes/hermes-agent —
# переустанавливается по пинованной версии из install.sh), кэши
# (audio/image/cache), временные sessions/sandboxes/pending_messages —
# это runtime-мусор, не пользовательские данные.
set -euo pipefail

SECRETS_DIR=/etc/helm/secrets
export RESTIC_REPOSITORY="rclone:yandex:helm-backup"
export RESTIC_PASSWORD_FILE="$SECRETS_DIR/restic_password"

# НАЙДЕНО 01.09.2026: два живых прогона подряд упали на одном и том же —
# webdav.yandex.ru отвечал "500 Internal Server Error" и "timeout
# awaiting response headers" на 2-3 чанка (разные хэши каждый раз, не
# один и тот же повреждённый объект), restic сдавался после ~15 минут
# повторов. Квота (1TB+ свободно), лок репозитория и сама доступность
# rclone:yandex — проверены, не причина: это Yandex WebDAV, не отвечающий
# вовремя под нагрузкой на отдельные запросы. restic вызывает `rclone
# serve restic` как бэкенд — RCLONE_* переменные окружения читает сам
# бинарь rclone независимо от способа запуска. Дефолты (timeout 5m,
# low-level-retries 10) недостаточны для этого конкретного WebDAV —
# даём больше времени на ответ и больше попыток на отдельный запрос.
export RCLONE_TIMEOUT=10m
export RCLONE_CONTIMEOUT=2m
export RCLONE_LOW_LEVEL_RETRIES=20

WORKDIR=$(mktemp -d /var/lib/helm-guardian/backup-XXXXXX)
trap 'rm -rf "$WORKDIR"' EXIT

# 1. Postgres — вся кластерная выгрузка (все БД + роли), не по одной БД:
#    §2 простота — не поддерживаем отдельный список имён БД в двух местах.
docker exec helm-postgres-1 pg_dumpall -U helm > "$WORKDIR/postgres-dumpall.sql"

# 2. Консистентные снапшоты SQLite (Hermes работает в WAL-режиме — сырое
#    копирование .db без .backup рискует захватить БД в промежуточном
#    состоянии между .db/.db-wal/.db-shm).
mkdir -p "$WORKDIR/hermes-sqlite"
sqlite3 /home/helm/.hermes/kanban.db ".backup '$WORKDIR/hermes-sqlite/kanban.db'"
sqlite3 /home/helm/.hermes/state.db ".backup '$WORKDIR/hermes-sqlite/state.db'"

# 3. Выгрузка workflow n8n (§17.5, §23: «23:30 daily backup + n8n export»).
#    Неудача выгрузки не должна валить бэкап: n8n может лежать, а
#    Postgres и Hermes при этом обязаны быть сохранены.
if ! python3 /opt/helm/scripts/n8n-workflows.py export; then
  echo "ВНИМАНИЕ: выгрузка workflow n8n не удалась, бэкап продолжается"
fi

# 4. Каталог данных Forgejo (§18.7: repos + config + attachments/PR
#    metadata). БД Forgejo отдельно не нужна — она в том же кластере
#    Postgres и уже попала в pg_dumpall выше.
#
#    Путь резолвится через docker, а не пишется константой: расположение
#    каталога volume зависит от data-root демона, и захардкоженный
#    /var/lib/docker/... молча перестал бы существовать при его смене —
#    restic пропустил бы несуществующий путь, а бэкап считался бы удачным.
FORGEJO_DATA=$(docker volume inspect -f '{{.Mountpoint}}' helm_forgejo_data)
if [ ! -d "$FORGEJO_DATA" ]; then
  echo "каталог данных Forgejo не найден: $FORGEJO_DATA" >&2
  exit 1
fi

restic backup \
  "$WORKDIR/postgres-dumpall.sql" \
  "$WORKDIR/hermes-sqlite" \
  "$FORGEJO_DATA" \
  /opt/helm/n8n/exports \
  /opt/helm/config \
  /opt/helm/guardian \
  "$SECRETS_DIR" \
  /home/helm/.hermes/.env \
  /home/helm/.hermes/config.yaml \
  /home/helm/.hermes/auth.json \
  /home/helm/.hermes/channel_directory.json \
  /home/helm/.hermes/gateway_state.json \
  /home/helm/.hermes/SOUL.md \
  /home/helm/.hermes/cron \
  /home/helm/.hermes/hooks \
  /home/helm/.hermes/pairing \
  /home/helm/.hermes/platforms \
  /home/helm/.hermes/plugins \
  /home/helm/.hermes/profiles \
  /home/helm/.hermes/memories \
  /opt/helm-knowledge \
  --exclude 'skills' \
  --exclude 'derived' \
  --tag helm-daily

# Ретеншен: спека не задаёт конкретное число дней для бэкапов (только для
# n8n-executions в §25.6) — 7 daily / 4 weekly / 3 monthly разумный
# дефолт, не окончательное решение.
restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 3 --prune

# Guardian (§25) читает mtime этого файла как возраст последнего бэкапа.
touch /var/lib/helm-guardian/last-backup

echo "BACKUP DONE"
