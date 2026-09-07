#!/usr/bin/env bash
# HELM · как исполняются «сколько» и «все» на живом корпусе. Чтение.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.probe import probe
from helm_core.knowledge.query_spec import build_query_spec
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
tenant = bind_knowledge_user(session, None)

QUESTIONS = [
    "Сколько всего пунктов в аптечке?",
    "Перечисли все пункты дорожной аптечки",
    "Сколько у меня каналов?",
    "Какой у меня был холестерин в последний раз?",
]
for q in QUESTIONS:
    spec = build_query_spec(q, tenant_id=tenant)
    try:
        r = probe(session, query=q)
    except Exception as exc:
        print(f"\n{q}\n  операция={spec.operation} ПАДЕНИЕ {type(exc).__name__}: {exc}")
        session.rollback(); bind_knowledge_user(session, tenant); continue
    text = (r.answer_text or "").replace("\n", " ")[:210]
    print(f"\n{q}\n  операция={spec.operation} кандидатов={len(r.candidates)} "
          f"{r.outcome}/{r.mode}\n  {text}")
    session.rollback(); bind_knowledge_user(session, tenant)
PYEOF
rc=$?
if [ "$rc" -ne 0 ]; then echo "############ ПРОВАЛ (код $rc) ############"; exit "$rc"; fi
echo "############ ГОТОВО ############"
