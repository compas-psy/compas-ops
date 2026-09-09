#!/usr/bin/env bash
# HELM · импорт сценариев n8n и проверка на тестовом календаре.
#
# ЗАЧЕМ ИМЕННО ТАК. Обслуживание доставляет на сервер РОВНО ОДИН файл,
# поэтому оба сценария лежат тут же, дословно из helm/tree/n8n/. Это
# дублирование, и оно намеренное: альтернатива — тянуть JSON с GitHub
# на сервер, то есть завести ещё один канал доставки ради двух файлов.
#
# ЧТО ПРОВЕРЯЕТСЯ. «Тестовый календарь» отдаёт синтетический ICS: два
# события на сегодня и одно на завтра. Завтрашнее в план дня попасть НЕ
# должно — на нём и проверяется отбор по дате. Настоящий календарь для
# этого не нужен и не трогается.
#
# КЛЮЧ. /etc/helm/secrets/n8n_api_key читается host-скриптом, в вывод не
# попадает и в аргументы команд не передаётся.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

if ! sudo test -r /etc/helm/secrets/n8n_api_key; then
  echo "нет /etc/helm/secrets/n8n_api_key — импортировать нечем"
  exit 1
fi

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT
cat > "$WORKDIR/kalendar-na-segodnya.json" <<'DAYEOF'
{
  "name": "Календарь на сегодня",
  "active": false,
  "settings": {
    "executionOrder": "v1",
    "timezone": "Europe/Moscow",
    "saveManualExecutions": true
  },
  "nodes": [
    {
      "id": "a1000000-0000-4000-8000-000000000001",
      "name": "Спросили события",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [0, 0],
      "webhookId": "calendar-today",
      "parameters": {
        "httpMethod": "GET",
        "path": "calendar-today",
        "responseMode": "responseNode",
        "authentication": "headerAuth",
        "options": {}
      },
      "notes": "Спрашивает HELM, а не n8n сам присылает. Так секрет HELM в n8n не попадает вовсе."
    },
    {
      "id": "a1000000-0000-4000-8000-000000000002",
      "name": "Забрать календарь",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [220, 0],
      "onError": "continueRegularOutput",
      "parameters": {
        "url": "={{ $env.HELM_CALENDAR_ICS_URL }}",
        "options": {
          "timeout": 15000,
          "response": { "response": { "responseFormat": "text" } }
        }
      },
      "notes": "Единственное место, где нужен настоящий доступ. Пока его нет — сюда ставится файл тестового календаря (см. ранбук). Ошибка не обрывает сценарий: план дня без календаря лучше отсутствия плана."
    },
    {
      "id": "a1000000-0000-4000-8000-000000000003",
      "name": "События на сегодня",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [440, 0],
      "parameters": {
        "jsCode": "// Разбор ICS без библиотек: формат построчный, а тянуть зависимость\n// ради него — лишнее (§2 CLAUDE.md).\n//\n// ЧЕСТНЫЙ ОТКАЗ ВМЕСТО ПУСТОГО СПИСКА. Календарь не ответил — это НЕ\n// «сегодня ничего нет». Разница видна владельцу: пустой день и\n// недоступный календарь — разные сообщения.\nconst input = $input.first().json;\nconst body = typeof input.data === 'string' ? input.data : (input.body || '');\nif (!body || input.error) {\n  return [{ json: { ok: false, reason: 'календарь недоступен', events: [] } }];\n}\n\nconst today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Moscow' });\nconst unfolded = body.replace(/\\r\\n[ \\t]/g, '').split(/\\r?\\n/);\nconst events = [];\nlet current = null;\nfor (const line of unfolded) {\n  if (line === 'BEGIN:VEVENT') { current = {}; continue; }\n  if (line === 'END:VEVENT') {\n    if (current && current.date === today) {\n      events.push({ time: current.time || '', summary: current.summary || '(без названия)' });\n    }\n    current = null;\n    continue;\n  }\n  if (!current) continue;\n  const colon = line.indexOf(':');\n  if (colon < 0) continue;\n  const name = line.slice(0, colon).split(';')[0];\n  const value = line.slice(colon + 1);\n  if (name === 'SUMMARY') current.summary = value;\n  if (name === 'DTSTART') {\n    current.date = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;\n    current.time = value.length > 8 ? `${value.slice(9, 11)}:${value.slice(11, 13)}` : '';\n  }\n}\nevents.sort((a, b) => a.time.localeCompare(b.time));\nreturn [{ json: { ok: true, date: today, events } }];\n"
      }
    },
    {
      "id": "a1000000-0000-4000-8000-000000000004",
      "name": "Ответить HELM",
      "type": "n8n-nodes-base.respondToWebhook",
      "typeVersion": 1.1,
      "position": [660, 0],
      "parameters": { "respondWith": "allIncomingItems", "options": {} }
    }
  ],
  "connections": {
    "Спросили события": { "main": [[{ "node": "Забрать календарь", "type": "main", "index": 0 }]] },
    "Забрать календарь": { "main": [[{ "node": "События на сегодня", "type": "main", "index": 0 }]] },
    "События на сегодня": { "main": [[{ "node": "Ответить HELM", "type": "main", "index": 0 }]] }
  }
}
DAYEOF
cat > "$WORKDIR/testovyy-kalendar.json" <<'TESTEOF'
{
  "name": "Тестовый календарь",
  "active": false,
  "settings": { "executionOrder": "v1", "timezone": "Europe/Moscow" },
  "nodes": [
    {
      "id": "b2000000-0000-4000-8000-000000000001",
      "name": "Отдать тестовый ICS",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [0, 0],
      "webhookId": "test-calendar",
      "parameters": {
        "httpMethod": "GET",
        "path": "test-calendar",
        "responseMode": "responseNode",
        "options": {}
      },
      "notes": "Существует ради проверки сценария плана дня без доступа к настоящему календарю. Выключается одним переключателем — тем и проверяется ветка «календарь недоступен»."
    },
    {
      "id": "b2000000-0000-4000-8000-000000000002",
      "name": "Прочитать файл",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [220, 0],
      "parameters": {
        "jsCode": "// Даты подставляются на лету: календарь с зашитым числом\n// протух бы назавтра и проверял бы не то.\nconst pad = (n) => String(n).padStart(2, '0');\nconst now = new Date();\nconst d = (offset) => {\n  const x = new Date(now);\n  x.setDate(x.getDate() + offset);\n  return `${x.getFullYear()}${pad(x.getMonth() + 1)}${pad(x.getDate())}`;\n};\nconst ics = [\n  'BEGIN:VCALENDAR',\n  'VERSION:2.0',\n  'PRODID:-//HELM//Тестовый календарь//RU',\n  'BEGIN:VEVENT',\n  'UID:helm-test-1@cmpas.ru',\n  `DTSTART;TZID=Europe/Moscow:${d(0)}T100000`,\n  'SUMMARY:Созвон по проекту',\n  'END:VEVENT',\n  'BEGIN:VEVENT',\n  'UID:helm-test-2@cmpas.ru',\n  `DTSTART;TZID=Europe/Moscow:${d(0)}T150000`,\n  'SUMMARY:Забрать анализы',\n  'END:VEVENT',\n  'BEGIN:VEVENT',\n  'UID:helm-test-3@cmpas.ru',\n  `DTSTART;VALUE=DATE:${d(1)}`,\n  'SUMMARY:Завтрашнее событие — в сегодняшний план попасть не должно',\n  'END:VEVENT',\n  'END:VCALENDAR',\n].join('\\r\\n');\nreturn [{ json: { data: ics } }];\n"
      }
    },
    {
      "id": "b2000000-0000-4000-8000-000000000003",
      "name": "Ответить",
      "type": "n8n-nodes-base.respondToWebhook",
      "typeVersion": 1.1,
      "position": [440, 0],
      "parameters": {
        "respondWith": "text",
        "responseBody": "={{ $json.data }}",
        "options": {}
      }
    }
  ],
  "connections": {
    "Отдать тестовый ICS": { "main": [[{ "node": "Прочитать файл", "type": "main", "index": 0 }]] },
    "Прочитать файл": { "main": [[{ "node": "Ответить", "type": "main", "index": 0 }]] }
  }
}
TESTEOF

echo
echo "############ ИМПОРТ ############"
sudo N8N_KEY_FILE=/etc/helm/secrets/n8n_api_key python3 - "$WORKDIR" <<'PYEOF'
import json, os, sys, urllib.error, urllib.request
from pathlib import Path

API = "http://127.0.0.1:5678/api/v1"
key = Path(os.environ["N8N_KEY_FILE"]).read_text(encoding="utf-8").strip()


def call(method, path, body=None):
    request = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-N8N-API-KEY": key, "Content-Type": "application/json"},
        method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, {"raw": raw.decode(errors="replace")[:300]}
    except Exception as error:  # noqa: BLE001
        return 0, {"error": f"{type(error).__name__}: {error}"}


status, existing = call("GET", "/workflows")
if status != 200:
    sys.exit(f"n8n не отвечает на /workflows: HTTP {status} {existing}")
by_name = {item["name"]: item["id"] for item in (existing or {}).get("data", [])}
print(f"  сценариев в n8n до импорта: {len(by_name)}")

for filename in ("testovyy-kalendar.json", "kalendar-na-segodnya.json"):
    document = json.loads((Path(sys.argv[1]) / filename).read_text(encoding="utf-8"))
    name = document["name"]
    # n8n принимает только эти поля; active выставляется отдельным вызовом.
    payload = {k: document[k] for k in ("name", "nodes", "connections", "settings")
               if k in document}
    if name in by_name:
        status, body = call("PUT", f"/workflows/{by_name[name]}", payload)
        action = "обновлён"
    else:
        status, body = call("POST", "/workflows", payload)
        action = "создан"
    if status not in (200, 201):
        print(f"  {name}: ОШИБКА HTTP {status} {body}")
        continue
    identifier = (body or {}).get("id") or by_name.get(name)
    by_name[name] = identifier
    print(f"  {name}: {action}, id={identifier}")

# Тестовый календарь включается: без активации его вебхук не отвечает.
test_id = by_name.get("Тестовый календарь")
if test_id:
    status, body = call("POST", f"/workflows/{test_id}/activate")
    print(f"  Тестовый календарь: активация HTTP {status}")
PYEOF

echo
echo "############ ЧТО ОТДАЁТ ТЕСТОВЫЙ КАЛЕНДАРЬ ############"
sudo docker compose exec -T n8n sh -c \
  'wget -q -O - http://127.0.0.1:5678/webhook/test-calendar 2>&1' | sed 's/^/  /'

echo
echo "############ СЦЕНАРИИ ПОСЛЕ ############"
sudo N8N_KEY_FILE=/etc/helm/secrets/n8n_api_key python3 - <<'PYEOF'
import json, os, urllib.request
from pathlib import Path
key = Path(os.environ["N8N_KEY_FILE"]).read_text(encoding="utf-8").strip()
request = urllib.request.Request("http://127.0.0.1:5678/api/v1/workflows",
                                 headers={"X-N8N-API-KEY": key})
with urllib.request.urlopen(request, timeout=30) as response:
    for item in json.load(response).get("data", []):
        print(f"  {item['name']}: active={item['active']} id={item['id']}")
PYEOF
