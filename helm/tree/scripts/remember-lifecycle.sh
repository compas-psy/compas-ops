#!/usr/bin/env bash
# HELM · догнать записи «Запомни», сделанные до общего жизненного цикла,
# и проверить сценарий владельца на живых данных.
#
# Обратимо и идемпотентно: создаются производные данные (источник, чанки,
# эмбеддинги, задание на семантику) из уже сохранённого текста. Сам текст
# памяти не меняется, ничего не удаляется.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Догоняющий проход по памяти + проверка сценария B17."""
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.memory import backfill_memory_sources
from helm_core.knowledge.probe import probe
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeMemory

QUESTION = "Дай ссылку на мой канал B17"

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

before = session.scalar(
    select(func.count()).select_from(KnowledgeMemory)
    .where(KnowledgeMemory.source_id.is_(None))) or 0
print(f"############ ДОГОНЯЮЩИЙ ПРОХОД ############")
print(f"  записей памяти без источника до: {before}")

done, remaining = backfill_memory_sources(session)
print(f"  догнано: {done}   осталось: {remaining}")

# Тенант привязывается заново: backfill коммитит, а привязка тенанта
# живёт внутри транзакции (set_config(..., true)).
tenant = bind_knowledge_user(session, None)
chunks = session.scalar(
    select(func.count()).select_from(KnowledgeChunk)
    .where(KnowledgeChunk.text.ilike("%b17%"))) or 0
print(f"  чанков со словом b17 теперь: {chunks}")

print("\n############ СЦЕНАРИЙ ВЛАДЕЛЬЦА ############")
for question in (QUESTION, "какие у меня есть ссылки на каналы"):
    tenant = bind_knowledge_user(session, None)
    result = probe(session, query=question)
    print(f"\n  вопрос: {question}")
    print(f"  исход: {result.outcome}  режим: {result.mode}")
    print(f"  ответ: {(result.answer_text or '')[:600]}")
    for item in result.sources[:5]:
        print(f"    источник: {item}")
    session.rollback()

session.rollback()
PYEOF
rc=$?

if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi

echo "############ ГОТОВО ############"
