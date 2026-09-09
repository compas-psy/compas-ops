#!/usr/bin/env bash
# HELM · гейт универсальности, этап 5: сквозные срезы живьём.
#
# §4 мандата: PASS только если через ОДИН executor проходят все три
# среза. Здесь они и стоят рядом — включая те, что сегодня не выполнимы:
# срез, который нельзя прогнать, называется вслух, а не выпадает из
# отчёта молча.
#
# Вопросы идут через бот (`/hooks/max`), не прямым вызовом probe().
# Внутренняя сводка исполнителя печатается ОТДЕЛЬНО и подписана как
# внутренний слой — она не выдаётся за пользовательскую приёмку.
# Имён в ней нет по построению (`as_public_dict`, CLAUDE.md §5.2).
set -uo pipefail
cd /opt/helm/compose || exit 1

CHAT_ID=777

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

dc() { sudo docker compose exec -T helm-core "$@"; }

ask() {
  local text="$1" mid secret owner payload response task_id start
  mid="gate.$(date +%s%N)"
  secret=$(sudo cat /etc/helm/secrets/max_webhook_secret 2>/dev/null || echo "")
  owner=$(sudo cat /etc/helm/secrets/max_owner_id 2>/dev/null || echo 0)
  payload=$(python3 -c '
import json, sys
print(json.dumps({"update_type": "message_created", "message": {
    "sender": {"user_id": int(sys.argv[3])},
    "recipient": {"chat_id": int(sys.argv[4]), "chat_type": "dialog"},
    "body": {"mid": sys.argv[2], "seq": 1, "text": sys.argv[1]}}}))
' "$text" "$mid" "$owner" "$CHAT_ID")
  echo
  echo "── ЗАПРОС: $text"
  start=$(date +%s)
  response=$(timeout 200 dc python3 - "$payload" "$secret" <<'PYEOF'
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
    print(f'{{"status": "ОШИБКА", "detail": "{type(exc).__name__}"}}')
PYEOF
)
  echo "    исход: ${response:-(timeout 200 с оборвал)}"
  echo "    заняло: $(( $(date +%s) - start )) с"
  task_id=$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("task_id",""))' 2>/dev/null || echo "")
  echo "    ОТВЕТ, который увидит владелец:"
  if [ -z "$task_id" ]; then
    echo "     (исход не порождает исходящего сообщения)"
    return
  fi
  dc python3 -m helm_core.knowledge.acceptance_probe outbox "$CHAT_ID" "knowledge-probe:$task_id"
}

echo
echo "############ СРЕЗ A: PERSON + роль + признак ############"
ask "каких врачей я посещал?"

echo
echo "── ВНУТРЕННИЙ СЛОЙ (не пользовательская приёмка): сводка исполнителя"
dc python3 - <<'PYEOF'
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.query_router import DOCTORS, answer_structural

session = sessionmaker(bind=create_engine(get_settings().database_url,
                                          pool_pre_ping=True))()
answer = answer_structural(session, DOCTORS, question="каких врачей я посещал?")
session.rollback()
print(json.dumps(answer.as_public_dict(), ensure_ascii=False, indent=2))
PYEOF

echo
echo "############ СРЕЗ B: решение по проектному документу ############"
dc python3 - <<'PYEOF'
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.models import (KnowledgeSemanticJob, KnowledgeSemanticWindow,
                              KnowledgeSource)
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url,
                                          pool_pre_ping=True))()
bind_knowledge_user(session, None)
source = session.scalars(
    select(KnowledgeSource)
    .where(KnowledgeSource.original_filename == "MASTER_TZ.md")
    .order_by(KnowledgeSource.created_at.desc()).limit(1)).one_or_none()
if source is None:
    print("  источника нет — срез B не выполним")
    raise SystemExit(0)
job = session.scalars(select(KnowledgeSemanticJob)
                      .where(KnowledgeSemanticJob.source_id == source.id)
                      .order_by(KnowledgeSemanticJob.created_at.desc())
                      .limit(1)).one_or_none()
windows = session.scalar(
    select(func.count()).select_from(KnowledgeSemanticWindow)
    .where(KnowledgeSemanticWindow.semantic_run_id == job.semantic_run_id)
) if job is not None and job.semantic_run_id else 0
status = job.status if job is not None else "задания нет"
print(f"  семантика MASTER_TZ.md: {status}, окон {windows}")
if status != "done":
    print("  СРЕЗ B НЕ ВЫПОЛНЯЕТСЯ: без законченного разбора вопрос пойдёт")
    print("  обычным поиском, и назвать это проходом гейта нельзя.")
PYEOF

echo
echo "############ СРЕЗ C: организация + роль + интервал ############"
echo "  НЕ ВЫПОЛНЯЕТСЯ: источника нет. Резюме владельца не найдено ни в"
echo "  одном доступном репозитории (ilyamartynov.ru — приложение записи)."
echo "  Файл есть только у владельца; загрузка — вложением в бот."
