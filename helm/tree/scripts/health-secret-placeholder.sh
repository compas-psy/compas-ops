#!/bin/bash
# ADR-005/P12, шаг 1 из 4. Создаёт ПУСТОЙ файл-плейсхолдер секрета — сам
# пароль этот скрипт не трогает и не генерирует (то делает scripts/
# setup-health-role.sh отдельно, позже). Нужен ДО раскатки нового
# docker-compose.yml: Docker secrets типа `file:` требуют физического
# файла для самого старта контейнера, не только для чтения значения —
# без этого шага `docker compose up` для helm-core/helm-knowledge-worker
# упадёт на попытке смонтировать несуществующий secret.
# Идемпотентен: существующий файл (пустой или уже с паролем) не трогает.
# Запускается на сервере: bash /tmp/recon.sh
set -euo pipefail

SECRET_FILE=/etc/helm/secrets/health_database_url

if [ -e "$SECRET_FILE" ]; then
  echo "$SECRET_FILE уже существует — не трогаю"
else
  sudo touch "$SECRET_FILE"
  sudo chown root:helm-secrets "$SECRET_FILE"
  sudo chmod 640 "$SECRET_FILE"
  echo "$SECRET_FILE создан пустым (health-путь останется выключен, пока не прогнан setup-health-role.sh)"
fi

ls -la "$SECRET_FILE"
