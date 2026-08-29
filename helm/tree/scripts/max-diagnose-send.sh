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
# Пробует ДВЕ формы запроса, потому что первая живая проверка
# (29.08.2026, реальный chat_id из вебхука) вернула от MAX "Unknown
# recipient" именно в форме "body" — а MAX Bot API унаследован от
# TamTam Bot API, где chat_id/user_id передавались параметром URL, а не
# полем JSON-тела (риск был явно помечен как непроверенный в ADR-020).
#
# Запускается внутри контейнера helm-core тем же кодом, что и боевая
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
import urllib.parse
import urllib.request

from helm_core.channels.max import API_BASE, ssl_context

chat_id, text = sys.argv[1], sys.argv[2]
with open('/run/secrets/max_bot_token', encoding='utf-8') as f:
    token = f.read().strip()


def attempt(label, url, body_obj):
    body = json.dumps(body_obj).encode('utf-8')
    request = urllib.request.Request(
        url, data=body, method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': token},
    )
    print(f'--- {label}: {url} body={body_obj} ---')
    try:
        with urllib.request.urlopen(request, timeout=10, context=ssl_context()) as response:
            print('HTTP', response.status)
            print(response.read().decode())
            return True
    except urllib.error.HTTPError as exc:
        print('HTTP', exc.code)
        print(exc.read().decode())
        return False


# Форма 1: chat_id в теле JSON — то, что сейчас шлёт боевой код.
if attempt('body', API_BASE + '/messages', {'chat_id': chat_id, 'text': text}):
    sys.exit(0)

# Форма 2: chat_id query-параметром, текст в теле — наследие TamTam.
query = urllib.parse.urlencode({'chat_id': chat_id})
if attempt('query', f'{API_BASE}/messages?{query}', {'text': text}):
    sys.exit(0)

sys.exit(1)
" "$1" "$2"
