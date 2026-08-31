#!/bin/bash
# Живая проверка E13 слоя 1 (knowledge_relations, детерминированно, без
# LLM): ingest_text() с реальным [[wikilink]] в тексте. Транзакция
# откатывается — тестовые данные не остаются в базе (тот же контракт, что
# verify-z2-rephrase.sh для E12).
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail
cd /opt/helm/compose || exit 1

echo '=== E13 слой 1: [[wikilink]] в тексте -> knowledge_relations ==='
sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.ingest import ingest_text
from helm_core.models import KnowledgeRelation

engine = create_engine(get_settings().database_url)
with Session(engine) as s:
    source = ingest_text(
        s, domain="general",
        text="Первая заметка ссылается на [[Вторая заметка]] в тексте.",
        original_filename="e13-verify-first.md",
    )
    s.flush()
    rows = s.query(KnowledgeRelation).filter(
        KnowledgeRelation.source_id == source.id
    ).all()
    print("source_id:", source.id)
    print("relations found:", len(rows))
    for r in rows:
        print("from_id:", r.from_id, "| to_id:", r.to_id,
              "| relation_type:", r.relation_type, "| evidence_type:", r.evidence_type)
    s.rollback()
    print("транзакция откачена — тестовые данные не остались в базе")
PY
