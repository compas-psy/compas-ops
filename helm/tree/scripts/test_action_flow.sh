#!/bin/bash
# Живой смоук-тест A-DoD п.4-6 через реальный internal API (не unit-тесты):
#   п.4: GREEN/YELLOW действие исполняется сразу же при propose()
#   п.5: RED без approval остаётся PENDING, чужая identity получает 403
#   п.6: повторный propose() того же действия не исполняет его дважды
#        (RED физически не может завершиться прямо сейчас — единственный
#        RED-fixture требует непустой ALLOWED_PUBLIC_CHANNELS, который
#        намеренно пуст до P10; exact-once проверяется на том же
#        механизме идемпотентности через GREEN, см. распоряжение
#        владельца от 29.08.2026)
set -euo pipefail

CP=http://127.0.0.1:8080
SECRET=$(sudo cat /etc/helm/secrets/hermes_service_hmac)
OWNER_ID=$(sudo cat /etc/helm/secrets/telegram_owner_id)
# Без task_id: Approval.task_id — внешний ключ на tasks.id, выдуманный
# UUID без реальной задачи упал бы на ограничении FK. Смоук-тест самого
# action registry/approval-потока не нуждается в привязке к задаче.

sign_and_post() {
  local path="$1" body="$2"
  local ts sig
  ts=$(date +%s.%N)
  sig=$(printf '%s\0%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')
  curl -s -X POST "$CP$path" \
    -H "Content-Type: application/json" \
    -H "X-Helm-Timestamp: $ts" \
    -H "X-Helm-Signature: $sig" \
    -d "$body"
}

echo "== A-DoD п.4: GREEN (notify_owner) исполняется сразу =="
GREEN=$(sign_and_post /internal/actions/propose \
  '{"action_type":"notify_owner","payload":{"text":"смоук-тест GREEN"}}')
echo "$GREEN"

echo "== A-DoD п.4: YELLOW (kanban_snapshot) исполняется сразу =="
YELLOW=$(sign_and_post /internal/actions/propose \
  '{"action_type":"kanban_snapshot","payload":{"reason":"смоук-тест YELLOW"}}')
echo "$YELLOW"

echo "== A-DoD п.5: RED (publish_public_content) остаётся PENDING =="
RED=$(sign_and_post /internal/actions/propose \
  '{"action_type":"publish_public_content","payload":{"channel":"tg_test","body":"смоук-тест RED"}}')
echo "$RED"
RED_ID=$(echo "$RED" | python3 -c "import json,sys; print(json.load(sys.stdin)['approval_id'])")

echo "== A-DoD п.5: чужая identity получает отказ =="
WRONG_DECISION=$(sign_and_post "/internal/approvals/$RED_ID/decision" \
  '{"approve":true,"decided_by":"tg:999999999","channel":"telegram"}')
echo "$WRONG_DECISION"

echo "== A-DoD п.6: повторный propose() того же GREEN — не второй эффект =="
GREEN_AGAIN=$(sign_and_post /internal/actions/propose \
  '{"action_type":"notify_owner","payload":{"text":"смоук-тест GREEN"}}')
echo "$GREEN_AGAIN"

echo "== бонус: решение верным владельцем — precondition (пустой allowlist) перепроверяется прямо перед исполнением =="
RIGHT_DECISION=$(sign_and_post "/internal/approvals/$RED_ID/decision" \
  "{\"approve\":true,\"decided_by\":\"$OWNER_ID\",\"channel\":\"telegram\"}")
echo "$RIGHT_DECISION"

echo "DONE"
