#!/bin/bash
# Живой смоук-тест HELM Knowledge Probe (P8.5, ТЗ §14.11) через реальный
# Postgres и реальный internal HTTP API — не pytest, доказывает, что
# схема/код/wiring реально работают на этом сервере после деплоя.
#
# 1) запрос без ответа в базе -> NEEDS_REASONING
# 2) ingest тестовой записи ВНУТРИ уже запущенного контейнера helm-core
#    (тот же helm_core, та же БД, что у приложения — не отдельный движок)
# 3) запрос, пересекающийся с записью -> LOCAL_ANSWER с цитируемым источником
#
# Тестовая запись помечена явно ("HELM deploy smoke test") и легко
# находится/удаляется отдельно — не выдаётся за настоящее знание (§5.1).
set -euo pipefail

CP=http://127.0.0.1:8080
SECRET=$(sudo cat /etc/helm/secrets/hermes_service_hmac)
TMP1=$(mktemp)
TMP2=$(mktemp)
trap 'rm -f "$TMP1" "$TMP2"' EXIT

sign_and_post() {
  local path="$1" body="$2" out="$3"
  local ts sig
  ts=$(date +%s.%N)
  sig=$(printf '%s\0%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')
  curl -sS -X POST "$CP$path" \
    -H "Content-Type: application/json" \
    -H "X-Helm-Timestamp: $ts" \
    -H "X-Helm-Signature: $sig" \
    -d "$body" -o "$out"
}

echo "== 1. Запрос, которому взяться неоткуда -> NEEDS_REASONING =="
sign_and_post /internal/knowledge/probe '{"query":"какая погода на Марсе прямо сейчас"}' "$TMP1"
cat "$TMP1"; echo
python3 <<PYEOF
import json
with open("$TMP1") as f:
    d = json.load(f)
assert d["outcome"] == "NEEDS_REASONING", d
print("OK: NEEDS_REASONING")
PYEOF

echo "== 2. Ingest тестовой записи внутри контейнера helm-core =="
cd /opt/helm/compose
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from helm_core.config import Settings
from helm_core.knowledge.ingest import ingest_text

settings = Settings()
engine = create_engine(settings.database_url)
with Session(engine) as session:
    source = ingest_text(
        session, domain="engineering",
        text="HELM deploy smoke test: пробный факт для проверки Knowledge Probe после деплоя P8.5.",
        original_filename="deploy-smoke-test.md",
    )
    session.commit()
    print(f"ingested source_id={source.id}")
PYEOF

echo "== 3. Запрос теми же словами -> LOCAL_ANSWER =="
sign_and_post /internal/knowledge/probe '{"query":"какой пробный факт для проверки после деплоя"}' "$TMP2"
cat "$TMP2"; echo
python3 <<PYEOF
import json
with open("$TMP2") as f:
    d = json.load(f)
assert d["outcome"] == "LOCAL_ANSWER", d
assert "deploy-smoke-test.md" in d["answer_text"], d
print("OK: LOCAL_ANSWER, источник процитирован")
PYEOF

echo "DONE"
