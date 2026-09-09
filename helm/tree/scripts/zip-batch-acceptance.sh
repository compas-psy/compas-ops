#!/usr/bin/env bash
# HELM · пользовательская приёмка #26: ZIP-пачка, «Запомни», выдача оригинала.
#
# ЗАЧЕМ ОТДЕЛЬНО ОТ user-acceptance.sh. Там уже проверены вопросы,
# «Запомни» и просьба отдать оригинал, но НЕ проверена загрузка пачкой:
# путь «архив → вопрос о домене → распаковка → ответ по содержимому»
# живьём не проходили ни разу. Здесь он проходится целиком, вместе с
# двумя другими действиями, чтобы приёмка #26 закрывалась одним прогоном.
#
# ЧЕРЕЗ КАКОЙ ВХОД. `/hooks/max` — тот же пользовательский вход, что и в
# user-acceptance.sh: та же память, тот же probe, тот же тенант, тот же
# режим оплаты. Прямой вызов probe() здесь не используется.
#
# ЕДИНСТВЕННОЕ, ЧТО ЗДЕСЬ ПОДДЕЛАНО, — ОТКУДА БЕРУТСЯ БАЙТЫ АРХИВА.
# `download_attachment()` скачивает вложение по URL, который присылает
# сам MAX. Аккаунта MAX у агента нет, поэтому архив выкладывается на
# локальный HTTP внутри контейнера и URL указывает туда. Всё остальное —
# разбор апдейта, ZIP-preflight, диалог о домене, распаковка,
# регистрация, разбор воркером, ответ по содержимому — настоящее.
#
# ЧТО ПОПАДАЕТ В ПАМЯТЬ И КАК УБИРАЕТСЯ ЗА СОБОЙ. Документы пачки —
# приёмочные, они описывают сами себя и ничего не утверждают о делах
# владельца. В конце прогона источники, созданные ЭТОЙ пачкой,
# отключаются штатной `disable_created_sources()` (§14.5.2, мягкая
# блокировка, не удаление файлов), и тот же вопрос перестаёт находить
# ответ — это и проверка отката, и уборка за собой.
#
# Секреты читаются на сервере и НЕ печатаются (CLAUDE.md §5.4).
set -uo pipefail
cd /opt/helm/compose || exit 1

STAMP=$(date -u +%Y%m%d-%H%M%S)
CHAT_ID=777
ZIP_PORT=8099
ZIP_DIR=/tmp/ua-zip
ZIP_NAME=acceptance-batch.zip

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
    with urllib.request.urlopen(req, timeout=300) as resp:
        print(resp.read().decode()[:400])
except Exception as exc:  # noqa: BLE001 — диагностика приёмки
    print(f'{{"status": "ОШИБКА", "detail": "{type(exc).__name__}: {exc}"}}')
PYEOF
}

update_json() {
  # $1 — текст, $2 — mid, $3 — json-массив вложений (или пусто)
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
  local text="$1" mid start response reference
  mid="ua.$(date +%s%N)"
  echo
  echo "── ЗАПРОС: $text"
  start=$(date +%s)
  response=$(post_max "$(update_json "$text" "$mid" "")" | tr -d '\r')
  echo "    исход: $response"
  echo "    заняло: $(( $(date +%s) - start )) с"
  reference=$(reference_for "$response" "$mid")
  echo "    ОТВЕТ, который увидит владелец:"
  if [ -z "$reference" ]; then
    echo "     (исход не порождает исходящего сообщения)"
    return
  fi
  show_outbox "$reference"
}

echo
echo "############ 0. ВХОД СВОБОДЕН ############"
dc python3 - <<'PYEOF'
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.models import (KnowledgeIngestBatch, KnowledgeBatchStatus,
                              KnowledgePendingAttachment)
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)
pending = session.scalars(select(KnowledgePendingAttachment)
                          .where(KnowledgePendingAttachment.channel == "max")).all()
waiting = session.scalars(select(KnowledgeIngestBatch).where(
    KnowledgeIngestBatch.channel == "max",
    KnowledgeIngestBatch.status == KnowledgeBatchStatus.WAITING_DOMAIN)).all()
print(f"  незакрытых одиночных вложений: {len(pending)}")
print(f"  архивов, ждущих домена: {len(waiting)}")
if pending or waiting:
    print("  ВНИМАНИЕ: незакрытый диалог перехватит ответ про домен — прогон будет неверным")
PYEOF

echo
echo "############ 1. СБОРКА ПАЧКИ ############"
dc python3 - "$STAMP" <<'PYEOF'
import io, pathlib, sys, zipfile

stamp = sys.argv[1]
directory = pathlib.Path("/tmp/ua-zip")
directory.mkdir(parents=True, exist_ok=True)

nested = io.BytesIO()
with zipfile.ZipFile(nested, "w") as inner:
    inner.writestr("внутри.txt", "вложенный архив распаковке не подлежит\n")

pasport = f"""# Паспорт приёмочной пачки HELM

Это приёмочный документ: он описывает сам себя и ничего не утверждает о
делах владельца.

Идентификатор пачки: ПРИЁМКА-ZIP-{stamp}.
Инвентарный номер пачки: 4417.
Домен пачки: engineering.
Пачка собрана для проверки пути «архив — вопрос о домене — распаковка —
ответ по содержимому».
"""

sostav = f"""# Состав приёмочной пачки ПРИЁМКА-ZIP-{stamp}

Записей в архиве: 4.
Документов Markdown: 2.
Вложенных архивов: 1 — распаковке не подлежит.
Исполняемых файлов: 1 — обработке не подлежит.

Ожидаемый результат: к обработке принято 2 документа, две остальные
записи пропущены по правилам безопасности архива.
"""

archive = directory / "acceptance-batch.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
    bundle.writestr("Приёмка/паспорт-пачки.md", pasport)
    bundle.writestr("Приёмка/состав-пачки.md", sostav)
    bundle.writestr("Приёмка/вложенный.zip", nested.getvalue())
    bundle.writestr("Приёмка/установка.sh", "#!/bin/sh\necho приёмка\n")
print(f"  архив: {archive} ({archive.stat().st_size} байт), записей 4")
PYEOF

# Раздача архива живёт ровно один прогон: сервер сам гасит себя через
# 10 минут, поэтому убирать за ним отдельной командой (и держать в
# контейнере pkill) не нужно.
sudo docker compose exec -d helm-core python3 -c "
import functools, http.server, socketserver, threading
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory='$ZIP_DIR')
socketserver.TCPServer.allow_reuse_address = True
srv = socketserver.TCPServer(('127.0.0.1', $ZIP_PORT), handler)
threading.Timer(600, srv.shutdown).start()
srv.serve_forever()
" >/dev/null 2>&1
sleep 3
dc python3 -c "
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:$ZIP_PORT/$ZIP_NAME', timeout=10) as r:
    print('  раздача архива:', r.status, len(r.read()), 'байт')
"

echo
echo "############ 2. ПАЧКА ПРИШЛА В БОТ ############"
ZIP_MID="ua.zip.$STAMP"
ATTACH=$(python3 -c "
import json
print(json.dumps([{'type': 'file', 'filename': 'Приёмочная-пачка.zip',
                   'payload': {'url': 'http://127.0.0.1:$ZIP_PORT/$ZIP_NAME'}}]))
")
STAGE=$(post_max "$(update_json "" "$ZIP_MID" "$ATTACH")" | tr -d '\r')
echo "── исход: $STAGE"
BATCH_ID=$(printf '%s' "$STAGE" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("batch_id",""))' 2>/dev/null)
echo "── ОТВЕТ, который увидит владелец:"
if [ -n "$BATCH_ID" ]; then
  show_outbox "batch-staged:$BATCH_ID"
else
  echo "     (batch_id не получен — дальше идти незачем)"
  exit 1
fi

echo
echo "############ 3. ОТВЕТ ПРО ДОМЕН ############"
DOMAIN_MID="ua.dom.$STAMP"
DOMAIN=$(post_max "$(update_json "engineering" "$DOMAIN_MID" "")" | tr -d '\r')
echo "── ЗАПРОС: engineering"
echo "── исход: $DOMAIN"
echo "── ОТВЕТ, который увидит владелец:"
show_outbox "batch-queued:$BATCH_ID:$DOMAIN_MID"

echo
echo "############ 4. РАЗБОР ПАЧКИ ############"
for _ in $(seq 1 40); do
  DONE=$(dc python3 - "$BATCH_ID" <<'PYEOF'
import sys
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.models import (BATCH_ITEM_TERMINAL_STATUSES, KnowledgeBatchItem,
                              KnowledgeIngestBatch)
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)
batch = session.get(KnowledgeIngestBatch, __import__("uuid").UUID(sys.argv[1]))
items = session.scalars(select(KnowledgeBatchItem)
                        .where(KnowledgeBatchItem.batch_id == batch.id)
                        .order_by(KnowledgeBatchItem.ordinal)).all()
terminal = all(i.status in BATCH_ITEM_TERMINAL_STATUSES for i in items)
print("да" if terminal and items else "нет")
PYEOF
)
  [ "$(printf '%s' "$DONE" | tr -d '\r\n ')" = "да" ] && break
  sleep 15
done

dc python3 - "$BATCH_ID" <<'PYEOF'
import sys, uuid
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.models import KnowledgeBatchItem, KnowledgeIngestBatch, KnowledgeSource
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)
batch = session.get(KnowledgeIngestBatch, uuid.UUID(sys.argv[1]))
print(f"  архив: {batch.archive_filename}  статус: {batch.status}  домен: {batch.domain}")
print(f"  всего членов: {batch.total_members}  к обработке: {batch.eligible_members}")
print(f"  готово: {batch.ready_count}  дублей: {batch.duplicate_count}  "
      f"ошибок: {batch.failed_count}  карантин: {batch.quarantine_count}  "
      f"пропущено: {batch.skipped_count}  чанков всего: {batch.chunk_count_total}")
print(f"  ссылка финального уведомления: knowledge_batch_final:{batch.id}:{batch.completion_revision}")
for item in session.scalars(select(KnowledgeBatchItem)
                            .where(KnowledgeBatchItem.batch_id == batch.id)
                            .order_by(KnowledgeBatchItem.ordinal)).all():
    source = session.get(KnowledgeSource, item.source_id) if item.source_id else None
    name = source.original_filename if source else "—"
    print(f"    {item.ordinal}. {item.archive_member_name_normalized} → {item.status}"
          f"  чанков: {item.chunks}  источник: {name}"
          f"  создан пачкой: {item.source_created_by_batch}"
          f"{'  ошибка: ' + item.error_code if item.error_code else ''}")
PYEOF

FINAL_REF=$(dc python3 - "$BATCH_ID" <<'PYEOF'
import sys, uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.models import KnowledgeIngestBatch
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)
batch = session.get(KnowledgeIngestBatch, uuid.UUID(sys.argv[1]))
print(f"knowledge_batch_final:{batch.id}:{batch.completion_revision}")
PYEOF
)
echo "── ФИНАЛЬНОЕ УВЕДОМЛЕНИЕ, которое увидит владелец:"
show_outbox "$(printf '%s' "$FINAL_REF" | tr -d '\r\n')"

echo
echo "############ 5. ВОПРОС ПО СОДЕРЖИМОМУ ПАЧКИ ############"
ask "какой инвентарный номер у приёмочной пачки?"

echo
echo "############ 6. ВЫДАЧА ОРИГИНАЛА ИЗ ПАЧКИ ############"
ask "отдай оригинал паспорт-пачки.md"

echo
echo "############ 7. «ЗАПОМНИ» И ОБРАТНОЕ ИЗВЛЕЧЕНИЕ ############"
ask "Запомни: контрольное слово приёмки — ЛАНДЫШ-$STAMP"
ask "какое контрольное слово приёмки?"

# «Забудь …» в /hooks/max разбирается ВНУТРИ ветки вложений и pending-
# диалогов (hooks.py:220 — условие входа), а не до неё, как «Запомни».
# Без незакрытого диалога сообщение до `try_admin_command()` не доходит.
# Проверяем это живьём, а не по чтению кода: у Telegram-пути своя точка
# входа (`/internal/knowledge/admin`), и вывод одного канала на другой
# не переносится.
ask "Забудь контрольное слово приёмки"
ask "какое контрольное слово приёмки?"

echo
echo "── УБОРКА ЗАМЕТКИ ПРИЁМКИ (не проверка канала, а уборка за собой)"
dc python3 - <<'PYEOF'
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.admin import try_admin_command
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)
outcome = try_admin_command(session, text="Забудь контрольное слово приёмки")
session.commit()
print(f"  исход: {outcome.status}")
for line in (outcome.text or "").splitlines():
    print(f"    {line}")
PYEOF

echo
echo "############ 8. УБОРКА ЗА СОБОЙ И ПРОВЕРКА ОТКАТА ############"
dc python3 - "$BATCH_ID" <<'PYEOF'
import sys, uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.batch_intake import disable_created_sources
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)
count = disable_created_sources(session, uuid.UUID(sys.argv[1]))
session.commit()
print(f"  отключено источников, созданных пачкой: {count}")
PYEOF
ask "какой инвентарный номер у приёмочной пачки?"

echo
echo "############ 9. ПЛАТНЫЕ ВЫЗОВЫ ЗА ЧАС ############"
dc python3 -m helm_core.knowledge.acceptance_probe paid
