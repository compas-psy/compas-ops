#!/usr/bin/env bash
# HELM · новое правило нарезки против нынешнего, на живом корпусе. Чтение.
#
# Замер 05.09.2026 (прогон 307) показал корень плохих ответов: единица
# поиска — строка бланка. 953 чанка на 90 источников, медиана 65
# символов, 250 короче двадцати, 390 не предложения.
#
# `chunking.rechunk()` написан, но В ПУТЬ ЗАГРУЗКИ НЕ ВКЛЮЧЁН. Сначала
# замер на тех же данных и по тем же признакам, что и 05.09: стало
# лучше или просто иначе. Урок §7.1 того же разбора — мерить продакшн, а
# не копию алгоритма, поэтому обе функции берутся из выкаченного кода.
#
# Ничего не пишет: чанки в базе не трогаются.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Старое и новое разбиение бок о бок. Ничего не пишет."""
import statistics
from collections import Counter

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.answer_format import is_quotable
from helm_core.knowledge.chunking import rechunk
from helm_core.knowledge.ingest import split_chunks
from helm_core.knowledge.semantic_pilot import source_text
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource
from helm_core.models.base import KnowledgeStatus

BUCKETS = ((0, 20), (20, 40), (40, 80), (80, 200), (200, 10 ** 9))


def profile(chunks):
    lengths = [len(c) for c in chunks]
    buckets = Counter()
    for length in lengths:
        for low, high in BUCKETS:
            if low <= length < high:
                buckets[(low, high)] += 1
                break
    not_a_sentence = sum(
        1 for c in chunks if len(c) < 120 and not c.rstrip().endswith((".", "!", "?", ";")))
    all_caps = sum(1 for c in chunks
                   if [ch for ch in c if ch.isalpha()]
                   and all(ch.isupper() for ch in c if ch.isalpha()))
    return {
        "чанков": len(chunks),
        "медиана": int(statistics.median(lengths)) if lengths else 0,
        "среднее": int(statistics.mean(lengths)) if lengths else 0,
        "максимум": max(lengths) if lengths else 0,
        "buckets": buckets,
        "не предложения": not_a_sentence,
        "всё заглавными": all_caps,
        "проходит is_quotable": sum(1 for c in chunks if is_quotable(c)),
    }


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
bind_knowledge_user(session, None)

sources = session.scalars(
    select(KnowledgeSource).where(KnowledgeSource.status == KnowledgeStatus.ACTIVE)).all()

old_chunks, new_chunks, seen = [], [], 0
for source in sources:
    text = source_text(source)
    if not text:
        continue
    seen += 1
    old_chunks.extend(split_chunks(text))
    new_chunks.extend(rechunk(text))

old, new = profile(old_chunks), profile(new_chunks)

print("############ ЕДИНИЦА ПОИСКА: БЫЛО / СТАЛО ############")
print(f"  источников с текстом: {seen}")
print()
print(f"  {'признак':26} {'по пустой строке':>18} {'новое правило':>16}")
for key in ("чанков", "медиана", "среднее", "максимум",
            "не предложения", "всё заглавными", "проходит is_quotable"):
    print(f"  {key:26} {old[key]:>18} {new[key]:>16}")
print()
print(f"  {'длина чанка':26} {'по пустой строке':>18} {'новое правило':>16}")
for low, high in BUCKETS:
    name = f"{low}–{high}" if high < 10 ** 9 else f"{low} и больше"
    print(f"  {name:26} {old['buckets'][(low, high)]:>18} "
          f"{new['buckets'][(low, high)]:>16}")
print()
print("  Замер 05.09 для сверки: 953 чанка, медиана 65, короче 20 — 250,")
print("  не предложения — 390. Числа «по пустой строке» обязаны совпасть")
print("  с ними по порядку: иначе меряется не то, что было.")

session.rollback()
PYEOF

echo "############ ГОТОВО ############"
