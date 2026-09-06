#!/usr/bin/env bash
# HELM · пересборка поискового слоя новым правилом нарезки. ЗАПИСЬ.
#
# Обратимая запись производного состояния: чанки строятся из разобранного
# Markdown, который лежит в Vault, и пересобираются в любой момент.
# Поэтому `maintenance`, а не `destructive` — но точка возврата перед
# запуском снимается, как и для любой записи.
#
# Что меняется: `knowledge_chunks` и `health.knowledge_chunks` — текст,
# tsv и эмбеддинг. Граф semantic-v2, спаны, ревизии и слой личностей НЕ
# ТРОГАЮТСЯ: это другой слой, чанки он не читает.
#
# Эмбеддинг считается в той же операции, что и текст. Заменить чанки и
# оставить прежние векторы значит получить векторный поиск, отвечающий
# по тому, чего в базе больше нет, — ровно та авария, что случилась
# 06.09.2026 на слое личностей.
#
# Идемпотентно: повтор даёт то же число чанков, потому что прежние
# удаляются перед вставкой.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Пересобрать чанки всех живых источников. Пишет."""
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.chunking import store_chunks
from helm_core.knowledge.health_schema import (health_schema_configured,
                                               health_session, is_health_domain)
from helm_core.knowledge.semantic_pilot import source_text
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import HealthKnowledgeChunk, KnowledgeChunk, KnowledgeSource
from helm_core.models.base import KnowledgeStatus


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
Session = sessionmaker(bind=engine)


def counts():
    """Чанки в обеих схемах. Привязка тенанта транзакционно-локальная,
    поэтому берётся заново на каждый замер (урок приёмки P2, прогон 351:
    после commit() RLS спрятал собственную строку)."""
    session = Session()
    tenant = bind_knowledge_user(session, None)
    public = session.scalar(select(func.count()).select_from(KnowledgeChunk))
    health = 0
    if health_schema_configured():
        with health_session(tenant) as graph:
            health = graph.scalar(select(func.count()).select_from(HealthKnowledgeChunk))
    session.rollback()
    session.close()
    return public, health


before = counts()
print(f"было чанков: public={before[0]} health={before[1]}")

session = Session()
tenant = bind_knowledge_user(session, None)
sources = session.scalars(
    select(KnowledgeSource).where(KnowledgeSource.status == KnowledgeStatus.ACTIVE)).all()
plan = [(s.id, s.domain) for s in sources]
session.rollback()
session.close()

rebuilt = written = skipped = failed = 0
for source_id, domain in plan:
    # Своя транзакция на источник: сбой на одном не откатывает остальные,
    # и поиск ни в один момент не видит источник без чанков.
    session = Session()
    try:
        bind_knowledge_user(session, None)
        source = session.get(KnowledgeSource, source_id)
        text = source_text(source) if source else None
        if not text:
            skipped += 1
            session.rollback()
            continue
        written += store_chunks(session, source_id=source_id,
                                knowledge_user_id=source.knowledge_user_id,
                                domain=domain, text=text)
        session.commit()
        rebuilt += 1
    except Exception as exc:
        session.rollback()
        failed += 1
        # Только имя класса: текст ошибки может процитировать документ.
        print(f"  ПРОВАЛ на источнике {source_id}: {type(exc).__name__}")
    finally:
        session.close()

after = counts()
print(f"стало чанков: public={after[0]} health={after[1]}")
print()
print("############ ИТОГ ############")
print(f"  источников пересобрано:  {rebuilt}")
print(f"  без разобранного текста: {skipped}")
print(f"  упало:                   {failed}")
print(f"  чанков записано:         {written}")
print(f"  в базе после:            {after[0] + after[1]} (было {before[0] + before[1]})")
if written != after[0] + after[1]:
    print("  ВНИМАНИЕ: записано и лежит в базе — разные числа. Либо остались")
    print("  чанки источников, которых нет в плане, либо удаление не отработало.")
if failed:
    raise SystemExit(1)
PYEOF

echo "############ ГОТОВО ############"
