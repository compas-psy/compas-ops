#!/usr/bin/env bash
# HELM · доводка приёмки #26: работает ли §14.16 в MAX после правки.
#
# Прогон 507 показал, что «Забудь …» в MAX уходило в обычный поиск: бот
# отвечал самой заметкой. Правка поднимает разбор команды в условие
# входа, рядом с «Запомни». Проверка ровно эта и ничего больше — весь
# набор из zip-batch-acceptance.sh второй раз не гоняется.
set -uo pipefail
cd /opt/helm/compose || exit 1

STAMP=$(date -u +%Y%m%d-%H%M%S)
CHAT_ID=777

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "метка прогона: $STAMP"

dc() { sudo docker compose exec -T helm-core "$@"; }

post_max() {
  local payload_json="$1" secret
  secret=$(sudo cat /etc/helm/secrets/max_webhook_secret 2>/dev/null || echo "")
  dc python3 - "$payload_json" "$secret" <<'PYEOF'
import sys, urllib.request
payload, secret = sys.argv[1], sys.argv[2]
req = urllib.request.Request("http://127.0.0.1:8080/hooks/max",
                             data=payload.encode(), method="POST",
                             headers={"Content-Type": "application/json",
                                      "X-Max-Bot-Api-Secret": secret})
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        print(resp.read().decode()[:400])
except Exception as exc:  # noqa: BLE001 — диагностика приёмки
    print(f'{{"status": "ОШИБКА", "detail": "{type(exc).__name__}: {exc}"}}')
PYEOF
}

update_json() {
  local owner
  owner=$(sudo cat /etc/helm/secrets/max_owner_id 2>/dev/null || echo 0)
  python3 -c '
import json, sys
print(json.dumps({"update_type": "message_created", "message": {
    "sender": {"user_id": int(sys.argv[3])},
    "recipient": {"chat_id": int(sys.argv[4]), "chat_type": "dialog"},
    "body": {"mid": sys.argv[2], "seq": 1, "text": sys.argv[1]}}}))
' "$1" "$2" "$owner" "$CHAT_ID"
}

reference_for() {
  local response="$1" mid="$2" status task_id
  status=$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null)
  task_id=$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("task_id",""))' 2>/dev/null)
  case "$status" in
    local_answer|local_not_found|needs_clarification) echo "knowledge-probe:$task_id" ;;
    remember_*) echo "remember-${status#remember_}:$mid" ;;
    admin_*)    echo "admin-${status#admin_}:$mid" ;;
    *)          echo "" ;;
  esac
}

ask() {
  local text="$1" mid response reference
  mid="fc.$(date +%s%N)"
  echo
  echo "── ЗАПРОС: $text"
  response=$(post_max "$(update_json "$text" "$mid")" | tr -d '\r')
  echo "    исход: $response"
  reference=$(reference_for "$response" "$mid")
  echo "    ОТВЕТ, который увидит владелец:"
  if [ -z "$reference" ]; then
    echo "     (исход не порождает исходящего сообщения)"
    return
  fi
  dc python3 -m helm_core.knowledge.acceptance_probe outbox "$CHAT_ID" "$reference"
}

echo
echo "############ §14.16 В MAX ############"
ask "Запомни: контрольное слово приёмки — ЛАНДЫШ-$STAMP"
ask "какое контрольное слово приёмки?"
ask "Забудь контрольное слово приёмки"
ask "какое контрольное слово приёмки?"
ask "Верни в память контрольное слово приёмки"
ask "какое контрольное слово приёмки?"

echo
echo "############ УБОРКА ЗАМЕТКИ ПРИЁМКИ ############"
ask "Забудь контрольное слово приёмки"

echo
echo "############ СОСТОЯНИЕ ЗАМЕТКИ В БАЗЕ ############"
dc python3 - <<'PYEOF'
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.models import KnowledgeMemory
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)
rows = session.scalars(select(KnowledgeMemory)
                       .where(KnowledgeMemory.canonical_text.ilike("%контрольное слово%"))).all()
for row in rows:
    print(f"  {row.status}: {row.canonical_text}")
if not rows:
    print("  заметок с контрольным словом нет")
PYEOF
