#!/usr/bin/env bash
# HELM · что осталось висеть после сорванных голосовых + права. Чтение.
set -uo pipefail
cd /opt/helm/compose || exit 1
sudo find /opt/helm-knowledge/users -maxdepth 2 -type d -printf '%M %u:%g %f\n' | head -4
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeMemory, KnowledgePendingAttachment

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)
rows = session.scalars(select(KnowledgePendingAttachment)
                       .order_by(KnowledgePendingAttachment.created_at)).all()
print(f"ВИСЯЩИХ ВЛОЖЕНИЙ: {len(rows)}")
for r in rows:
    print(f"  {r.created_at:%d.%m %H:%M} {r.kind} {r.original_filename} "
          f"расшифровка={'есть' if r.transcript else 'нет'}")
mem = session.scalars(select(KnowledgeMemory).where(KnowledgeMemory.source_id.is_(None))).all()
print(f"ПАМЯТЬ БЕЗ ИСТОЧНИКА: {len(mem)}")
for m in mem[:5]:
    print(f"  {m.created_at:%d.%m %H:%M} {m.status} {m.canonical_text[:70]!r}")
session.rollback()
PYEOF
echo "############ ГОТОВО ############"
