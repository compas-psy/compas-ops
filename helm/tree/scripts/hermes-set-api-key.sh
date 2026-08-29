#!/bin/bash
# Кладёт значение секрета hermes_api_server_key в ~/.hermes/.env Hermes
# (ADR-020, шаг 2 hermes-enable-runbook.md). Отдельным скриптом, а не
# строкой в ssh: предыдущая попытка через `echo API_SERVER_KEY=$(sudo
# cat ...)` в PowerShell поймала ту же ловушку, что уже дважды ловилась
# на этом сервере (F-260828-01/02) — PowerShell разворачивает $(...)
# ЛОКАЛЬНО, ещё до отправки строки на сервер, поэтому "sudo cat" пытался
# выполниться на Windows, где sudo выключен, а на сервер ушла пустая
# строка "API_SERVER_KEY=". Идемпотентно: удаляет любую существующую
# строку API_SERVER_KEY= (в том числе ту самую пустую) перед добавлением
# новой — повторный запуск не плодит дубли.
#
# Запуск: sudo bash /tmp/hermes-set-api-key.sh
set -euo pipefail

KEY_FILE=/etc/helm/secrets/hermes_api_server_key
ENV_FILE=/home/helm/.hermes/.env

if [ ! -s "$KEY_FILE" ]; then
  echo "нет $KEY_FILE или он пуст — сначала шаг 1 hermes-enable-runbook.md" >&2
  exit 1
fi
KEY=$(cat "$KEY_FILE")

sed -i '/^API_SERVER_KEY=/d' "$ENV_FILE"
echo "API_SERVER_KEY=$KEY" >> "$ENV_FILE"
chown helm:helm "$ENV_FILE"

echo "готово, строк API_SERVER_KEY= в файле: $(grep -c '^API_SERVER_KEY=' "$ENV_FILE")"
