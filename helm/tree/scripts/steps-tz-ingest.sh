#!/usr/bin/env bash
# HELM · гейт универсальности, этап 3: реальный немедицинский документ
# в корпус через production ingestion.
#
# ЧТО ЗАГРУЖАЕТСЯ И ПОЧЕМУ ИМЕННО ЭТО. `QUERY_LAYER_UNIVERSALITY_
# 2026-09-05.md` §3.2 называет источник «ШАГИ_ТЗ_v3.0.md — реестр
# принятых продуктовых решений» и сам оговаривает, что точный файл
# подтверждается первым шагом этапа. Файла с таким именем в
# `compas-psy/steps` нет; реестр решений живёт как
# `docs/spec/MASTER_TZ.md` — «ШАГИ — ПОЛНОЕ ТЗ НА РЕАЛИЗАЦИЮ»,
# Implementation Specification 1.2 FROZEN от 29.08.2026, 172 956 байт.
# Это тот же документ по существу и другой по имени; имя в спеке было
# предположением, а не фактом.
#
# КОММИТ ЗАКРЕПЛЁН. Берётся не «последняя версия ветки», а ровно тот
# коммит, который прочитан и сверен: 87276b9f. SHA256 содержимого
# проверяется до загрузки — если байты другие, скрипт останавливается,
# а не грузит в память то, чего никто не читал.
#
# ЧЕРЕЗ КАКОЙ ПУТЬ. §3.3: «через тот же production ingestion pipeline, а
# не подготовленными фактами прямо в БД». Поэтому — обычным вложением в
# бот (`/hooks/max`), с обычным вопросом о домене и обычным ответом на
# него. Единственное, что подделано, — источник байтов вложения:
# локальный HTTP внутри контейнера вместо CDN MAX, аккаунта MAX у агента
# нет (то же ограничение, что в приёмке #26).
set -uo pipefail
cd /opt/helm/compose || exit 1

STAMP=$(date -u +%Y%m%d-%H%M%S)
CHAT_ID=777
PORT=8099
DIR=/tmp/ua-zip
NAME=MASTER_TZ.md
COMMIT=87276b9f46e072be8c8eccd31376db1115068e91
WANT_SHA=7eab727dd67fb5c76a37288fea5f7c624490f22adfc84d7f819bff22909c171e
URL="https://raw.githubusercontent.com/compas-psy/steps/$COMMIT/docs/spec/MASTER_TZ.md"

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "метка прогона: $STAMP"

dc() { sudo docker compose exec -T helm-core "$@"; }

echo
echo "############ 1. ФАЙЛ ПОЛУЧЕН И СВЕРЕН ############"
if ! curl -fsSL --max-time 120 "$URL" -o "/tmp/$NAME"; then
  echo "  НЕ СКАЧАЛОСЬ: $URL"
  echo "  Загрузка не выполнена. Это находка, а не повод грузить что-то другое."
  exit 1
fi
GOT_SHA=$(sha256sum "/tmp/$NAME" | cut -d' ' -f1)
echo "  байт: $(stat -c%s "/tmp/$NAME")"
echo "  sha256: $GOT_SHA"
if [ "$GOT_SHA" != "$WANT_SHA" ]; then
  echo "  ОЖИДАЛСЯ:  $WANT_SHA"
  echo "  Содержимое не то, что прочитано и сверено. Ничего не гружу."
  exit 1
fi
echo "  совпало с прочитанным — гружу"

echo
echo "############ 2. ФАЙЛ ВНУТРИ КОНТЕЙНЕРА ############"
dc mkdir -p "$DIR"
dc sh -c "cat > $DIR/$NAME" < "/tmp/$NAME"
dc sh -c "ls -l $DIR/$NAME"

# Раздача живёт один прогон и гасит себя сама — та же схема, что в
# приёмке #26, отдельной командой останова не нужна.
sudo docker compose exec -d helm-core python3 -c "
import functools, http.server, socketserver, threading
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory='$DIR')
socketserver.TCPServer.allow_reuse_address = True
srv = socketserver.TCPServer(('127.0.0.1', $PORT), handler)
threading.Timer(900, srv.shutdown).start()
srv.serve_forever()
" >/dev/null 2>&1
sleep 3
dc python3 -c "
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:$PORT/$NAME', timeout=10) as r:
    print('  раздача:', r.status, len(r.read()), 'байт')
"

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
    with urllib.request.urlopen(req, timeout=600) as resp:
        print(resp.read().decode()[:400])
except Exception as exc:  # noqa: BLE001 — диагностика загрузки
    print(f'{{"status": "ОШИБКА", "detail": "{type(exc).__name__}: {exc}"}}')
PYEOF
}

update_json() {
  local owner
  owner=$(sudo cat /etc/helm/secrets/max_owner_id 2>/dev/null || echo 0)
  python3 -c '
import json, sys
body = {"mid": sys.argv[2], "seq": 1, "text": sys.argv[1]}
if sys.argv[3]:
    body["attachments"] = json.loads(sys.argv[3])
print(json.dumps({"update_type": "message_created", "message": {
    "sender": {"user_id": int(sys.argv[4])},
    "recipient": {"chat_id": int(sys.argv[5]), "chat_type": "dialog"},
    "body": body}}))
' "$1" "$2" "${3:-}" "$owner" "$CHAT_ID"
}

show_outbox() {
  dc python3 -m helm_core.knowledge.acceptance_probe outbox "$CHAT_ID" "$1"
}

echo
echo "############ 3. ВЛОЖЕНИЕ ПРИШЛО В БОТ ############"
ATTACH=$(python3 -c "
import json
print(json.dumps([{'type': 'file', 'filename': '$NAME',
                   'payload': {'url': 'http://127.0.0.1:$PORT/$NAME'}}]))
")
MID="tz.$STAMP"
STAGE=$(post_max "$(update_json "" "$MID" "$ATTACH")" | tr -d '\r')
echo "── исход: $STAGE"
PENDING=$(printf '%s' "$STAGE" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("pending_id",""))' 2>/dev/null)
echo "── ОТВЕТ, который увидит владелец:"
if [ -z "$PENDING" ]; then
  echo "     (pending_id не получен — дальше идти незачем)"
  exit 1
fi
show_outbox "attachment-staged:$PENDING"

echo
echo "############ 4. ОТВЕТ ПРО ДОМЕН ############"
DOMAIN_MID="tz.dom.$STAMP"
DOMAIN=$(post_max "$(update_json "engineering" "$DOMAIN_MID" "")" | tr -d '\r')
echo "── ЗАПРОС: engineering"
echo "── исход: $DOMAIN"

echo
echo "############ 5. ЧТО ЛЕГЛО В КОРПУС ############"
dc python3 - "$NAME" <<'PYEOF'
import sys
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.models import (KnowledgeChunk, KnowledgeIngestJob, KnowledgeSemanticJob,
                              KnowledgeSemanticWindow, KnowledgeSource)
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)
source = session.scalars(
    select(KnowledgeSource)
    .where(KnowledgeSource.original_filename == sys.argv[1])
    .order_by(KnowledgeSource.created_at.desc()).limit(1)).one_or_none()
if source is None:
    print("  источник не найден — загрузка не дошла до корпуса")
    raise SystemExit(0)
chunks = session.scalar(select(func.count()).select_from(KnowledgeChunk)
                        .where(KnowledgeChunk.source_id == source.id))
print(f"  источник: {source.original_filename}  домен: {source.domain}  статус: {source.status}")
print(f"  id: {source.id}")
print(f"  чанков L1: {chunks}")
for job in session.scalars(select(KnowledgeIngestJob)
                           .where(KnowledgeIngestJob.source_id == source.id)).all():
    print(f"  разбор: {job.status}  ошибка: {job.error or '—'}")
for job in session.scalars(select(KnowledgeSemanticJob)
                           .where(KnowledgeSemanticJob.source_id == source.id)).all():
    windows = session.scalar(
        select(func.count()).select_from(KnowledgeSemanticWindow)
        .where(KnowledgeSemanticWindow.semantic_run_id == job.semantic_run_id)
    ) if job.semantic_run_id else 0
    print(f"  семантика: {job.status}  попыток: {job.attempts}  окон: {windows}"
          f"  ошибка: {job.error or '—'}")
PYEOF

echo
echo "Семантический разбор идёт в фоне и на документе такого размера"
echo "занимает часы. Состояние смотреть тем же разделом 5 повторно."
