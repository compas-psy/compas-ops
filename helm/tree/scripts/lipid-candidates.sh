#!/usr/bin/env bash
# HELM · доходит ли сам липидный профиль до кандидатов. Чтение.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Кандидаты на вопрос о липидном профиле: кто, с каким рангом, почему."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.answer_format import is_quotable
from helm_core.knowledge.probe import _health_lexical_search, _attach_dates
from helm_core.knowledge.tenancy import bind_knowledge_user

QUESTION = "Уровень холестерина по липидному профилю?"

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

hits = _health_lexical_search(query=QUESTION, knowledge_user_id=tenant)
print(f"############ КАНДИДАТОВ ЛЕКСИКИ: {len(hits)} ############")
_attach_dates(session, hits)
for item in sorted(hits, key=lambda h: h.rank, reverse=True):
    mark = "цитируем" if is_quotable(item.chunk_text) else "ОТБРАКОВАН"
    print(f"\n  ранг {item.rank:.5f} · {mark} · {item.original_filename}")
    print(f"    даты: документ {item.content_date}, сведения {item.fact_date}")
    print(f"    {item.chunk_text[:150]!r}")
session.rollback()
PYEOF
rc=$?
if [ "$rc" -ne 0 ]; then echo "############ ПРОВАЛ (код $rc) ############"; exit "$rc"; fi
echo "############ ГОТОВО ############"
