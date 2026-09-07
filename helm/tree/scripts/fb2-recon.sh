#!/usr/bin/env bash
# HELM · что на самом деле лежит в корпусе от книги в fb2 и от любого
# другого документа, разобранного в один фрагмент. Только чтение.
#
# Три вопроса, все три нужны ДО перезагрузки книги:
#   1. какие источники пришли как .fb2, каким парсером разобраны и
#      сколько фрагментов дали;
#   2. лежит ли в их фрагментах разметка вместо текста;
#   3. насколько это общая беда: сколько ВООБЩЕ источников имеют ровно
#      один фрагмент при большом объёме текста.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""fb2 и одно-фрагментные источники: факты из базы, не предположения."""
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeSource

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
bind_knowledge_user(session, None)

counts = (select(KnowledgeChunk.source_id.label("sid"),
                 func.count().label("chunks"),
                 func.sum(func.length(KnowledgeChunk.text)).label("chars"))
          .group_by(KnowledgeChunk.source_id).subquery())

print("############ 1. ИСТОЧНИКИ .fb2 ############")
rows = session.execute(
    select(KnowledgeSource.id, KnowledgeSource.original_filename, KnowledgeSource.parser,
           KnowledgeSource.status, counts.c.chunks, counts.c.chars)
    .outerjoin(counts, counts.c.sid == KnowledgeSource.id)
    .where(KnowledgeSource.raw_path.ilike("%.fb2"))).all()
if not rows:
    print("  ни одного — книга либо в другом домене, либо под другим расширением")
for source_id, filename, parser, status, chunks, chars in rows:
    print(f"  {filename or 'без имени'} | parser={parser} | {status} | "
          f"фрагментов={chunks or 0} | символов={chars or 0}")
    first = session.scalar(
        select(KnowledgeChunk.text).where(KnowledgeChunk.source_id == source_id)
        .order_by(KnowledgeChunk.ordinal).limit(1))
    if first:
        markup = first.count("<")
        print(f"    начало фрагмента: {first[:160]!r}")
        print(f"    угловых скобок «<» во фрагменте: {markup}")

print()
print("############ 2. ИСТОЧНИКИ С ОДНИМ ФРАГМЕНТОМ ############")
rows = session.execute(
    select(KnowledgeSource.original_filename, KnowledgeSource.parser, counts.c.chars)
    .join(counts, counts.c.sid == KnowledgeSource.id)
    .where(counts.c.chunks == 1, counts.c.chars > 2000)
    .order_by(counts.c.chars.desc()).limit(20)).all()
print(f"  один фрагмент при объёме больше 2000 символов: {len(rows)}")
for filename, parser, chars in rows:
    print(f"    {filename or 'без имени'} | parser={parser} | символов={chars}")

print()
print("############ 3. САМЫЕ ДЛИННЫЕ ФРАГМЕНТЫ КОРПУСА ############")
rows = session.execute(
    select(KnowledgeSource.original_filename, func.length(KnowledgeChunk.text))
    .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
    .order_by(func.length(KnowledgeChunk.text).desc()).limit(10)).all()
for filename, length in rows:
    print(f"    {length:>8} символов — {filename or 'без имени'}")
PYEOF
