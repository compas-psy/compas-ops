#!/bin/bash
# Убрать тестовые записи knowledge-worker-smoke-test.sh (P8.5.2) после
# подтверждённого живого теста.
set -euo pipefail

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

echo "DONE"
