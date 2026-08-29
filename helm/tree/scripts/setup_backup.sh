#!/bin/bash
# Разовая настройка backup-инфраструктуры (§26): rclone-remote на Яндекс
# Диск (WebDAV) + restic-репозиторий поверх него. Запускать один раз,
# от root. Идемпотентен — повторный запуск не портит уже настроенное.
set -euo pipefail

CREDS=/root/helm-bootstrap/backup_credentials
SECRETS_DIR=/etc/helm/secrets
RESTIC_PASSWORD_FILE="$SECRETS_DIR/restic_password"
REPO="rclone:yandex:helm-backup"

if ! command -v rclone >/dev/null 2>&1 || ! command -v restic >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq rclone restic
fi

# shellcheck disable=SC1090
source "$CREDS"
OBSCURED_PASS=$(rclone obscure "$YANDEX_WEBDAV_PASS")

rclone config create yandex webdav \
  url="$YANDEX_WEBDAV_URL" \
  vendor=other \
  user="$YANDEX_WEBDAV_USER" \
  pass="$OBSCURED_PASS" \
  --non-interactive >/dev/null

echo "rclone remote 'yandex' создан/обновлён"
rclone lsd yandex: >/dev/null
echo "rclone: соединение с Яндекс Диском подтверждено"

if [ ! -s "$RESTIC_PASSWORD_FILE" ]; then
  openssl rand -hex 32 > "$RESTIC_PASSWORD_FILE"
  chmod 600 "$RESTIC_PASSWORD_FILE"
  chown root:root "$RESTIC_PASSWORD_FILE"
  echo "restic_password сгенерирован — единственная копия, потеря = потеря доступа к бэкапам"
fi

export RESTIC_REPOSITORY="$REPO"
export RESTIC_PASSWORD_FILE

if restic snapshots >/dev/null 2>&1; then
  echo "restic-репозиторий уже инициализирован"
else
  restic init
  echo "restic-репозиторий инициализирован: $REPO"
fi

echo "SETUP DONE"
