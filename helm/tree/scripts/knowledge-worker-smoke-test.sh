#!/bin/bash
# Живой смоук-тест P8.5.2 (parser router + async worker) — доказывает
# ровно то, что НЕ проверено в песочнице разработки (нет доступа к
# huggingface.co для моделей Docling, нет доступа к Docker Hub для
# сборки образа): реальная сборка образа, реальный MarkItDown-разбор в
# запущенном контейнере, и — главное — реальная эскалация на Docling
# на файле, где MarkItDown извлекает испорченный текст.
#
# Ожидается, что /tmp/knowledge-worker-fixtures/ уже содержит fixture-файлы
# (см. шаг 0 в knowledge-worker-deploy-runbook.md).
set -euo pipefail

VAULT=/opt/helm-knowledge/raw/engineering
FIXTURES=/tmp/knowledge-worker-fixtures
TMP1=$(mktemp)
trap 'rm -f "$TMP1"' EXIT

echo "== 0. Файлы-фикстуры на месте в raw/ =="
sudo mkdir -p "$VAULT"
sudo cp "$FIXTURES/sample.docx" "$VAULT/smoke-test.docx"
sudo cp "$FIXTURES/sample_broken_font.pdf" "$VAULT/smoke-test-broken.pdf"
# НАЙДЕНО живым тестом: chown -R на ВЕСЬ /opt/helm-knowledge при каждом
# прогоне переприсваивает владельца файлам, которые в ПРЕДЫДУЩИЙ раз
# создал воркер (UID 10002) — обратно на хостового helm (UID 1000).
# При повторном прогоне с тем же содержимым файла (тот же SHA256, тот
# же детерминированный source_path) воркер натыкается на файл, который
# ему больше не принадлежит, только доступен по группе (644 — group
# только читает) — PermissionError на записи L1 SOURCE, не на чтении
# raw/. chown только вот эти два ЮЖЕ файла, не всё дерево.
sudo chown helm:helm "$VAULT/smoke-test.docx" "$VAULT/smoke-test-broken.pdf"

echo "== 1. Регистрируем оба файла через helm-knowledge-worker (там смонтирован /opt/helm-knowledge) =="
cd /opt/helm/compose
sudo docker compose exec -T helm-knowledge-worker python3 - > "$TMP1" <<'PYEOF'
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.ingest import register_file_for_ingest

settings = get_settings()
engine = create_engine(settings.database_url)
with Session(engine) as session:
    for name in ("smoke-test.docx", "smoke-test-broken.pdf"):
        result = register_file_for_ingest(
            session, domain="engineering",
            raw_path=Path(f"/opt/helm-knowledge/raw/engineering/{name}"),
            original_filename=name,
        )
        session.commit()
        print(f"{name}: source_id={result.source.id} job_id={result.job.id if result.job else None} created={result.created}")
PYEOF
cat "$TMP1"
# НАЙДЕНО живым тестом: старый запрос статуса матчил по original_filename,
# который НЕ уникален — leftover-строка с прошлого (не до конца
# убранного) прогона делила то же имя файла и путала статус-проверку
# ниже. Берём source_id ИМЕННО этой регистрации, не имя файла.
DOCX_SOURCE_ID=$(grep '^smoke-test.docx:' "$TMP1" | sed 's/.*source_id=\([^ ]*\).*/\1/')
PDF_SOURCE_ID=$(grep '^smoke-test-broken.pdf:' "$TMP1" | sed 's/.*source_id=\([^ ]*\).*/\1/')
echo "DOCX_SOURCE_ID=$DOCX_SOURCE_ID"
echo "PDF_SOURCE_ID=$PDF_SOURCE_ID"

echo "== 2. Ждём воркер (первый запуск Docling может качать модели с huggingface.co — даём до 3 минут) =="
for _ in $(seq 1 18); do
  PENDING=$(sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
    "select count(*) from knowledge_ingest_jobs j
     where j.source_id in ('$DOCX_SOURCE_ID', '$PDF_SOURCE_ID')
       and j.status in ('PENDING','RUNNING')")
  [ "$PENDING" = "0" ] && break
  sleep 10
done

echo "== 3. Статус задач =="
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select s.original_filename, s.parser, s.status as source_status, j.status as job_status, j.error
   from knowledge_ingest_jobs j join knowledge_sources s on s.id = j.source_id
   where j.source_id in ('$DOCX_SOURCE_ID', '$PDF_SOURCE_ID')
   order by j.created_at"

echo
echo "== 4. Ожидания: smoke-test.docx -> DONE (markitdown), файл L1 SOURCE существует =="
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select source_path from knowledge_sources where id = '$DOCX_SOURCE_ID'" > "$TMP1"
SOURCE_PATH=$(cat "$TMP1")
echo "source_path=$SOURCE_PATH"
sudo test -f "$SOURCE_PATH" && echo "OK: L1 SOURCE файл существует" || echo "FAIL: L1 SOURCE файл не найден"
sudo cat "$SOURCE_PATH"

echo
echo "== 5. smoke-test-broken.pdf: ожидается эскалация на Docling (DONE если Docling справился, NEEDS_REVIEW если тоже не прошёл gate) =="
echo "Смотри статус выше — 'docling' в колонке parser означает, что эскалация СРАБОТАЛА (это и есть непроверенная в песочнице часть)."

echo
echo "DONE"
