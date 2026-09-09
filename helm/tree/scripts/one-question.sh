#!/usr/bin/env bash
# HELM · ОДИН вопрос с жёстким ограничением времени.
#
# ЗАЧЕМ ОТДЕЛЬНО ОТ ПОЛНОЙ ПРИЁМКИ. Прогон 481 висел 31 минуту — дольше
# теоретического максимума семи вопросов по 180 секунд, то есть где-то
# не сработал даже клиентский таймаут. Мерить ответ надо так, чтобы
# замер сам не становился зависанием: один вопрос, 90 секунд потолка,
# и рядом — состояние Ollama, чтобы отличить «модель думает» от
# «модель занята чужой работой».
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ ЧЕМ ЗАНЯТА МОДЕЛЬ ДО ВОПРОСА ############"
sudo docker compose exec -T ollama sh -c 'ollama ps 2>&1' | sed 's/^/  /'
echo "— очередь семантики:"
sudo docker compose exec -T helm-core python3 -c "
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSemanticJob
s = sessionmaker(bind=create_engine(get_settings().database_url, future=True))()
bind_knowledge_user(s, None)
for status, count in s.execute(select(KnowledgeSemanticJob.status, func.count()).group_by(KnowledgeSemanticJob.status)).all():
    print(f'  {status}: {count}')
" 2>&1 | tail -6

echo
echo "############ ВОПРОС ############"
CHAT_ID=777
secret=$(sudo cat /etc/helm/secrets/max_webhook_secret 2>/dev/null || echo "")
owner=$(sudo cat /etc/helm/secrets/max_owner_id 2>/dev/null || echo 0)
mid="oneq.$(date +%s%N)"
text="какой у меня был холестерин в последний раз?"
payload=$(python3 -c '
import json, sys
print(json.dumps({"update_type": "message_created", "message": {
    "sender": {"user_id": int(sys.argv[3])},
    "recipient": {"chat_id": int(sys.argv[4]), "chat_type": "dialog"},
    "body": {"mid": sys.argv[2], "seq": 1, "text": sys.argv[1]}}}))
' "$text" "$mid" "$owner" "$CHAT_ID")

echo "── ЗАПРОС: $text"
start=$(date +%s)
response=$(timeout 100 sudo docker compose exec -T helm-core python3 - "$payload" "$secret" <<'PYEOF'
import sys, urllib.request
payload, secret = sys.argv[1], sys.argv[2]
req = urllib.request.Request("http://127.0.0.1:8080/hooks/max",
                             data=payload.encode(), method="POST",
                             headers={"Content-Type": "application/json",
                                      "X-Max-Bot-Api-Secret": secret})
try:
    with urllib.request.urlopen(req, timeout=90) as resp:
        print(resp.read().decode()[:300])
except Exception as exc:  # noqa: BLE001
    print(f'{{"status": "ОШИБКА", "detail": "{type(exc).__name__}"}}')
PYEOF
)
echo "    исход: ${response:-(timeout 100 с оборвал)}"
echo "    заняло: $(( $(date +%s) - start )) с"

task_id=$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("task_id",""))' 2>/dev/null || echo "")
if [ -n "$task_id" ]; then
  echo "    ОТВЕТ:"
  sudo docker compose exec -T helm-core \
    python3 -m helm_core.knowledge.acceptance_probe outbox "$CHAT_ID" "knowledge-probe:$task_id"
fi

echo
echo "############ ЧЕМ ЗАНЯТА МОДЕЛЬ ПОСЛЕ ############"
sudo docker compose exec -T ollama sh -c 'ollama ps 2>&1' | sed 's/^/  /'
