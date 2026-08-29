#!/bin/bash
# Разовая диагностика POST /v1/responses (ТЗ §10.2, ADR-020).
#
# Печатает СЫРОЙ JSON-ответ Hermes целиком: форма ответа подтверждена
# по исходникам (_handle_responses), но не проверена живьём — этот
# скрипт закрывает вопрос тем же приёмом, что раскрыл реальную форму
# вызова MAX API (max-diagnose-send.sh, F-260829-21): читать код мало,
# нужно увидеть настоящий ответ.
#
# Текст запроса намеренно нейтральный ("Скажи привет одним словом") —
# безопасно печатать ответ целиком, реальная переписка владельца сюда
# не попадает.
#
# Запуск: sudo /opt/helm/scripts/hermes-responses-diagnose.sh
set -euo pipefail

KEY_FILE=/etc/helm/secrets/hermes_api_server_key
if [ ! -s "$KEY_FILE" ]; then
  echo "нет $KEY_FILE или он пуст — сначала hermes-enable-runbook.md" >&2
  exit 1
fi
KEY=$(cat "$KEY_FILE")

echo "== POST http://127.0.0.1:8642/v1/responses =="
HTTP=$(curl -sS -o /tmp/hermes-responses.out -w '%{http_code}' \
  -X POST http://127.0.0.1:8642/v1/responses \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  --data-binary '{"model":"hermes-agent","input":"Скажи привет одним словом","conversation":"helm-diagnose-test"}' \
  --max-time 120)

echo "HTTP $HTTP"
python3 -m json.tool /tmp/hermes-responses.out || cat /tmp/hermes-responses.out
rm -f /tmp/hermes-responses.out

if [ "$HTTP" != "200" ]; then
  echo "не 200 — см. тело ответа выше" >&2
  exit 1
fi

echo
echo "Готово. Если в ответе виден output[].content[].type == \"output_text\","
echo "helm_core/hermes_bridge.py::_extract_reply_text написан верно."
echo "Если форма другая — вставь весь JSON выше в чат, поправим одну функцию."
