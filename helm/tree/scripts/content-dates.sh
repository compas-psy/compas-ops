#!/usr/bin/env bash
# HELM · проставить датам документов их настоящие даты и проверить
# «последний раз» на живых данных.
#
# Обратимо: `content_date` — производное поле, считается из текста
# источника, исходники не трогаются.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Даты документов + вопросы владельца о последнем анализе."""
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.backfill import backfill_content_dates
from helm_core.knowledge.probe import probe
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()

dated, unknown = backfill_content_dates(session)
print(f"############ ДАТЫ ДОКУМЕНТОВ ############")
print(f"  проставлено: {dated}   дату определить не удалось: {unknown}")

tenant = bind_knowledge_user(session, None)
total = session.scalar(select(func.count()).select_from(KnowledgeSource)
                       .where(KnowledgeSource.knowledge_user_id == tenant)) or 0
with_date = session.scalar(
    select(func.count()).select_from(KnowledgeSource)
    .where(KnowledgeSource.knowledge_user_id == tenant,
           KnowledgeSource.content_date.is_not(None))) or 0
print(f"  источников всего: {total}, с датой: {with_date}")

print("\n############ ВОПРОСЫ ВЛАДЕЛЬЦА ############")
for question in ("Какой у меня холестерин был в последний раз?",
                 "Уровень холестерина по липидному профилю?",
                 "Какое у меня было давление?"):
    tenant = bind_knowledge_user(session, None)
    result = probe(session, query=question)
    print(f"\n  вопрос: {question}")
    print(f"  исход: {result.outcome}  режим: {result.mode}")
    print(f"  ответ: {(result.answer_text or '')[:400]}")
    session.rollback()

session.rollback()
PYEOF
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi
echo "############ ГОТОВО ############"
