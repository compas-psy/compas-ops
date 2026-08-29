#!/bin/bash
# Прямая диагностика Yandex WebDAV через curl — код ответа + тело/заголовки
# ответа СЕРВЕРА (не наши учётные данные, безопасно выводить).
set -euo pipefail

CREDS=/root/helm-bootstrap/backup_credentials
# shellcheck disable=SC1090
source "$CREDS"

echo "== PROPFIND (обычный WebDAV-запрос листинга) =="
curl -s -o /tmp/webdav_body.txt -D /tmp/webdav_headers.txt \
  -u "${YANDEX_WEBDAV_USER}:${YANDEX_WEBDAV_PASS}" \
  -X PROPFIND -H "Depth: 1" \
  "${YANDEX_WEBDAV_URL}/" \
  -w 'HTTP_CODE=%{http_code}\n'

echo "== заголовки ответа =="
cat /tmp/webdav_headers.txt

echo "== тело ответа (первые 2000 байт) =="
head -c 2000 /tmp/webdav_body.txt
echo
rm -f /tmp/webdav_body.txt /tmp/webdav_headers.txt
