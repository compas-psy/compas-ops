#!/bin/bash
# Ежедневный бэкап (§26.1): PostgreSQL (все БД), Control Plane config,
# LiteLLM config, профили/state/kanban Hermes — в restic-репозиторий на
# Яндекс Диске. Запускается от root (helm-backup.service).
#
# Не бэкапится: сам установленный Hermes (~/.hermes/hermes-agent —
# переустанавливается по пинованной версии из install.sh), кэши
# (audio/image/cache), временные sessions/sandboxes/pending_messages —
# это runtime-мусор, не пользовательские данные.
set -euo pipefail

SECRETS_DIR=/etc/helm/secrets
export RESTIC_REPOSITORY="rclone:yandex:helm-backup"
export RESTIC_PASSWORD_FILE="$SECRETS_DIR/restic_password"

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

restic backup \
  "$WORKDIR/postgres-dumpall.sql" \
  "$WORKDIR/hermes-sqlite" \
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
  --exclude 'skills' \
  --tag helm-daily

# Ретеншен: спека не задаёт конкретное число дней для бэкапов (только для
# n8n-executions в §25.6) — 7 daily / 4 weekly / 3 monthly разумный
# дефолт, не окончательное решение.
restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 3 --prune

# Guardian (§25) читает mtime этого файла как возраст последнего бэкапа.
touch /var/lib/helm-guardian/last-backup

echo "BACKUP DONE"
