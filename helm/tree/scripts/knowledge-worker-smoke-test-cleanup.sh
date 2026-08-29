#!/bin/bash
# Убрать тестовые записи knowledge-worker-smoke-test.sh (P8.5.2) после
# подтверждённого живого теста.
set -euo pipefail

# НАЙДЕНО живым тестом: source_path (L1 SOURCE .md-файл) раньше не
# удалялся вовсе. Повторный прогон с тем же содержимым фикстуры даёт
# тот же SHA256 -> тот же детерминированный путь -> воркер натыкается
# на файл с прошлого прогона, которым уже не владеет (см. комментарий
# в knowledge-worker-smoke-test.sh) — PermissionError. Забираем пути
# ДО удаления строк, иначе взять их будет неоткуда.
SOURCE_PATHS=$(sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select source_path from knowledge_sources where original_filename in ('smoke-test.docx','smoke-test-broken.pdf')")

sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "delete from knowledge_chunks where source_id in (
     select id from knowledge_sources where original_filename in ('smoke-test.docx','smoke-test-broken.pdf')
   )"

sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "delete from knowledge_ingest_jobs where source_id in (
     select id from knowledge_sources where original_filename in ('smoke-test.docx','smoke-test-broken.pdf')
   )"

sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "delete from knowledge_sources where original_filename in ('smoke-test.docx','smoke-test-broken.pdf')"

sudo rm -f /opt/helm-knowledge/raw/engineering/smoke-test.docx \
           /opt/helm-knowledge/raw/engineering/smoke-test-broken.pdf

while IFS= read -r path; do
  [ -n "$path" ] && sudo rm -f "$path"
done <<< "$SOURCE_PATHS"

echo "DONE"
