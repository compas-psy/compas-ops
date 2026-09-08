#!/usr/bin/env bash
# HELM · дошёл ли исправленный парсер до корпуса. Только чтение.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ ОТМЕТКИ РАЗБОРА ПО ИСТОЧНИКАМ ############"
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.derivation import derivation_fingerprint
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource, KnowledgeStatus

session = sessionmaker(bind=create_engine(get_settings().database_url, future=True))()
bind_knowledge_user(session, None)
current = derivation_fingerprint()
print(f"  нынешний отпечаток: {current[:16]}…")
rows = session.execute(
    select(KnowledgeSource.derivation_fingerprint, func.count())
    .where(KnowledgeSource.status == KnowledgeStatus.ACTIVE)
    .group_by(KnowledgeSource.derivation_fingerprint)).all()
for fingerprint, count in rows:
    mark = "НЫНЕШНИЙ" if fingerprint == current else ("НЕ РАЗБИРАЛСЯ" if fingerprint is None else "прежний")
    shown = (fingerprint or "NULL")[:16]
    print(f"  {shown:20} {mark:15} источников {count}")
PYEOF
