#!/bin/bash
# Разовая диагностика отправки в MAX Bot API (ТЗ §10.3).
#
# Зачем отдельным скриптом, а не чтением боевого лога: dispatch.py
# намеренно не печатает тело ответа канала (F-260829-20 — провайдеры
# кладут туда эхо запроса, то есть потенциально текст сообщения
# владельца), поэтому диагностировать конкретный HTTP 400 по обычному
# логу нельзя в принципе. Здесь — наоборот, ручной разовый запуск с
# полным выводом: сообщение известно заранее (передаётся аргументом),
# ничего чужого не печатается.
#
# Запускается внутри контейнера helm-core, тем же кодом, что и боевая
# отправка (helm_core.channels.max: тот же API_BASE, та же ssl_context
# с доверием Минцифры) — чтобы результат был про формат запроса к MAX,
# а не про отличия диагностики от прода.
#
# Запуск: sudo /opt/helm/scripts/max-diagnose-send.sh CHAT_ID "ТЕКСТ"
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "использование: $0 CHAT_ID ТЕКСТ" >&2
  exit 1
fi

cd /opt/helm/compose
docker compose exec -T helm-core python3 -c "
import json
import sys
import urllib.error
import urllib.request

from helm_core.channels.max import API_BASE, ssl_context

chat_id, text = sys.argv[1], sys.argv[2]
with open('/run/secrets/max_bot_token', encoding='utf-8') as f:
    token = f.read().strip()

body = json.dumps({'chat_id': chat_id, 'text': text}).encode('utf-8')
request = urllib.request.Request(
    API_BASE + '/messages', data=body, method='POST',
    headers={'Content-Type': 'application/json', 'Authorization': token},
)
try:
    with urllib.request.urlopen(request, timeout=10, context=ssl_context()) as response:
        print('HTTP', response.status)
        print(response.read().decode())
except urllib.error.HTTPError as exc:
    print('HTTP', exc.code)
    print(exc.read().decode())
" "$1" "$2"
