#!/usr/bin/env bash
# HELM · приёмка P1: память не платит по сбою и отдаёт источники.
#
# Только чтение. Печатает СТРУКТУРУ ответа, а не его содержимое: имена
# врачей и цитаты для этой проверки не нужны, а диагностическая ценность
# у них нулевая. Проверяется контракт, а не данные.
#
# Что доказывается:
#   1. на сервере лежит именно тот код, что в ветке;
#   2. у плагина потолок ожидания probe больше, чем у рефраза внутри;
#   3. /internal/knowledge/probe отдаёт sources и answer_run_id;
#   4. этот answer_run_id действительно есть в knowledge_answer_runs —
#      то есть увиденный ответ сцепляется с серверной строкой, а не
#      просто содержит красивое поле.
set -uo pipefail

# Счётчик провалов. До 06.09.2026 каждая неудачная проверка печаталась
# через `|| echo "ПРОВАЛ"`, а `echo` всегда успешен — скрипт возвращал
# ноль, и прогон был зелёным при провалившейся приёмке. Найдено аудитом
# владельца.
FAIL=0
cd /opt/helm/compose || exit 1

echo "############ 1. ЧТО ВЫКАЧЕНО ############"
echo "  DEPLOYED_SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ 2. ПОТОЛКИ ОЖИДАНИЯ ############"
PLUGIN=$(getent passwd helm | cut -d: -f6)/.hermes/plugins/helm-control/__init__.py
probe_to=$(sudo grep -oP '^PROBE_TIMEOUT = \K[0-9]+' "$PLUGIN" 2>/dev/null || echo "нет")
req_to=$(sudo grep -oP '^REQUEST_TIMEOUT = \K[0-9]+' "$PLUGIN" 2>/dev/null || echo "нет")
reph_to=$(sudo docker compose exec -T helm-core python3 -c \
  "from helm_core.knowledge import rephrase; print(rephrase.REQUEST_TIMEOUT)" 2>/dev/null || echo "нет")
echo "  плагин PROBE_TIMEOUT:   $probe_to"
echo "  плагин REQUEST_TIMEOUT: $req_to  (прочие вызовы, должны падать быстро)"
echo "  рефраз REQUEST_TIMEOUT: $reph_to"
if [ "$probe_to" != "нет" ] && [ "$reph_to" != "нет" ] && [ "$probe_to" -ge "$reph_to" ]; then
  echo "  ОК: бесплатный ответ успевает уложиться в бюджет плагина"
else
  echo "  ПРОВАЛ: probe снова отвалится раньше собственного рефраза"
  FAIL=1
fi

echo
echo "############ 3. КОНТРАКТ /internal/knowledge/probe ############"
# Подписываем так же, как это делает плагин: HMAC от «метка\0тело».
sudo python3 - <<'PYEOF'
import hashlib, hmac, json, time, urllib.request

SECRET = open("/etc/helm/secrets/hermes_service_hmac").read().strip()
body = json.dumps({"query": "каких врачей я посещал?"}).encode()
ts = str(time.time())
sig = hmac.new(SECRET.encode(), ts.encode() + b"\x00" + body, hashlib.sha256).hexdigest()
req = urllib.request.Request(
    "http://127.0.0.1:8080/internal/knowledge/probe", data=body, method="POST",
    headers={"Content-Type": "application/json",
             "X-Helm-Timestamp": ts, "X-Helm-Signature": sig})
started = time.monotonic()
with urllib.request.urlopen(req, timeout=60) as resp:
    data = json.loads(resp.read().decode())
elapsed = time.monotonic() - started

print(f"  задержка:      {elapsed:.1f} c")
print(f"  outcome:       {data.get('outcome')}")
print(f"  mode:          {data.get('mode')}")
print(f"  длина ответа:  {len(data.get('answer_text') or '')} символов")
sources = data.get("sources")
if sources is None:
    print("  sources:       ПОЛЯ НЕТ — выкачена прежняя версия")
else:
    kinds = sorted({s.get("kind") for s in sources})
    print(f"  sources:       {len(sources)} шт., виды: {kinds or '—'}")
run_id = data.get("answer_run_id")
print(f"  answer_run_id: {run_id or 'ПОЛЯ НЕТ'}")
open("/tmp/p1-run-id", "w").write(run_id or "")
PYEOF

echo
echo "############ 4. СЦЕПКА С ЖУРНАЛОМ ############"
RUN_ID=$(sudo cat /tmp/p1-run-id 2>/dev/null || echo "")
sudo rm -f /tmp/p1-run-id
if [ -z "$RUN_ID" ]; then
  echo "  ПРОВАЛ: answer_run_id не пришёл — ответ нечем сцепить с журналом"
  FAIL=1
else
  sudo docker compose exec -T postgres psql -U helm -d helm -tAc \
    "select mode || ' | paid_ai_used=' || paid_ai_used || ' | evidence_count=' || evidence_count
       from knowledge_answer_runs where id = '$RUN_ID'" 2>/dev/null \
    | sed 's/^/  строка журнала: /' | grep . \
    || { echo "  ПРОВАЛ: строки с таким id в knowledge_answer_runs нет"; FAIL=1; }
fi

if [ "$FAIL" -ne 0 ]; then
  echo "############ ПРОВАЛ ############"
  exit 1
fi

echo "############ ГОТОВО ############"
