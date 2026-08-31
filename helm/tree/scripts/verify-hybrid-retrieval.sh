#!/bin/bash
# Живая проверка ADR-025 фаза 2 (pgvector hybrid retrieval) после выката
# 31.08.2026: тестовый чанк без общих словных корней с запросом должен
# находиться через _vector_search, где чисто лексический поиск отдал бы
# NEEDS_REASONING. Транзакция откатывается — тестовые данные не остаются
# в базе. Read-only с т.з. состояния БД, запускается на сервере:
#   bash /tmp/recon.sh
set -uo pipefail
cd /opt/helm/compose || exit 1

echo '=== hybrid retrieval: чанк без общих корней с запросом ==='
sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.probe import probe

engine = create_engine(get_settings().database_url)
with Session(engine) as s:
    ingest_text(s, domain="engineering", text="Мигрируем документооборот на новую CRM.")
    s.flush()
    result = probe(s, query="что там с системой учёта клиентов")
    print("outcome:", result.outcome)
    print("mode:", result.mode)
    print("evidence chunk_ids:", [e.chunk_id for e in result.evidence])
    print("evidence texts:", [e.chunk_text for e in result.evidence])
    s.rollback()
    print("транзакция откачена — тестовые данные не остались в базе")
PY

echo
echo '=== helm-embed: healthz ==='
sudo docker compose exec -T helm-embed python3 -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read().decode())"

echo
echo '=== helm-embed: резидентная память ==='
sudo docker stats --no-stream --format '{{.Name}}: {{.MemUsage}}' helm-helm-embed-1
