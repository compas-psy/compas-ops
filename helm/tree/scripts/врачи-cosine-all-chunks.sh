#!/bin/bash
# Реальный косинус (через embed_texts_or_none — тот же путь, что probe())
# между "каких врачей я посещал" и КАЖДЫМ health-чанком, без regex-фильтра
# кандидатов (raw dump уже показал: специальности реально есть в тексте —
# "ОСМОТР УРОЛОГА", "ОСМОТР ГАСТРОЭНТЕРОЛОГА" и т.д.).
#
# ВАЖНО: фильтр по domain='health' (ASCII), не по archive_filename='Врачи.zip'.
# Предыдущая версия с кириллическим литералом в WHERE нашла "0 чанков" —
# притом что тот же самый литерал через обычный psql -c по ssh находил их
# без проблем. Это воспроизводимый баг ИМЕННО в передаче кириллического
# строкового литерала внутри Python-источника через
# `docker compose exec -T ... python3 <<'PY'` — не баг в реальном
# коде поиска (probe.py никогда не печёт кириллицу в текст SQL-запроса,
# текст вопроса всегда идёт биндом через параметр функции). domain и все
# остальные фильтры здесь — чистый ASCII, чтобы не наступить на тот же
# баг снова.
#
# Цель: увидеть, где относительно MIN_COSINE_SIMILARITY=0.35 оказываются
# именно "голые" чанки-заголовки без слова "врач" — там, где лексика по
# определению их не найдёт и вся надежда на семантику.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

cd /opt/helm/compose
sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.embeddings import embed_texts_or_none

engine = create_engine(get_settings().database_url)

query = "каких врачей я посещал"
query_embedding = embed_texts_or_none([query])[0]
if query_embedding is None:
    print("embed-сервис недоступен ПРЯМО СЕЙЧАС")
    raise SystemExit(0)

print(f"запрос: {query!r} (repr, чтобы видеть реальные байты)")

with Session(engine) as s:
    rows = s.execute(text(
        """
        select c.id, c.text, c.embedding
        from knowledge_chunks c
        join knowledge_sources s on s.id = c.source_id
        where s.domain = 'health'
        order by c.source_id, c.ordinal
        """
    )).all()

print(f"всего health-чанков: {len(rows)}")

scored = []
for chunk_id, chunk_text, embedding in rows:
    if embedding is None:
        scored.append((None, chunk_id, chunk_text))
        continue
    dot = sum(x * y for x, y in zip(query_embedding, embedding))
    norm_a = sum(x * x for x in query_embedding) ** 0.5
    norm_b = sum(y * y for y in embedding) ** 0.5
    cos = dot / (norm_a * norm_b)
    scored.append((cos, chunk_id, chunk_text))

scored.sort(key=lambda t: (t[0] is None, -(t[0] or 0)))
for cos, chunk_id, chunk_text in scored:
    preview = chunk_text.replace("\n", " ")[:90]
    if cos is None:
        print(f"embedding=NULL  {chunk_id}  {preview!r}")
    else:
        flag = "OK " if cos >= 0.35 else "низко"
        print(f"cosine={cos:.4f} [{flag}]  {chunk_id}  {preview!r}")
PY
