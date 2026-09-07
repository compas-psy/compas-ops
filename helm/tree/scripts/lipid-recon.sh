#!/usr/bin/env bash
# HELM · где физически лежит 8.4 и где липидный профиль. Чтение.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Значения 8.1 и 8.4 и слово «липид» в чанках: где, в каком документе."""
import re

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_session
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (HealthKnowledgeChunk, HealthKnowledgeSourcePrivate,
                              KnowledgeSource)

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

with health_session(tenant) as graph:
    rows = graph.execute(
        select(HealthKnowledgeChunk.source_id, HealthKnowledgeChunk.text,
               HealthKnowledgeSourcePrivate.original_filename)
        .outerjoin(HealthKnowledgeSourcePrivate,
                   HealthKnowledgeChunk.source_id
                   == HealthKnowledgeSourcePrivate.source_id)).all()

print(f"############ ЧАНКОВ ВСЕГО (health): {len(rows)} ############")
for pattern, title in ((r"8[.,]4\b", "8.4"), (r"8[.,]1\b", "8.1"),
                       (r"липидн\w*\s+профил|липидограмм", "липидный профиль")):
    print(f"\n──── {title} ────")
    hits = 0
    for source_id, text, filename in rows:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            hits += 1
            start = max(0, match.start() - 90)
            snippet = text[start:match.end() + 30].replace("\n", " ")
            source = session.get(KnowledgeSource, source_id)
            stamp = source.content_date if source else None
            print(f"  {filename} ({stamp}): …{snippet}…")
            break
    if not hits:
        print("  не найдено ни в одном чанке")
PYEOF
rc=$?
if [ "$rc" -ne 0 ]; then echo "############ ПРОВАЛ (код $rc) ############"; exit "$rc"; fi
echo "############ ГОТОВО ############"
