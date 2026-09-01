#!/bin/bash
# Настоящая причина "0 чанков"/"0 совпадений" в двух предыдущих диагностиках
# (semantic-search-врачи-check.sh, врачи-cosine-all-chunks.sh) — НЕ кодировка
# и НЕ отсутствие данных: RLS (FORCE ROW LEVEL SECURITY, v3.8 Фаза 1). Обе
# сессии открывали create_engine(...)+Session(...) и сразу слали SELECT, не
# вызвав bind_knowledge_user() — единственная точка, которая ставит GUC
# app.current_knowledge_user_id, без которого RLS фильтрует ВСЕ строки
# tenant-scoped таблиц молча (пустой результат, не ошибка). vrachi-domain-check.sh
# (обычный psql — суперпользователь, RLS не подчиняется) уже подтвердил: 90
# health-источников, 367 чанков, 341 с embedding — данные и эмбеддинги на
# месте. Здесь — тот же вопрос, но через reальный bind_knowledge_user()+
# _vector_search(), без порога MIN_COSINE_SIMILARITY, чтобы увидеть, где
# именно оказываются "голые" чанки-специальности.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

cd /opt/helm/compose
sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.embeddings import embed_texts_or_none
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeSource
from sqlalchemy import select

engine = create_engine(get_settings().database_url)

query = "каких врачей я посещал"
query_embedding = embed_texts_or_none([query])[0]
if query_embedding is None:
    print("embed-сервис недоступен ПРЯМО СЕЙЧАС")
    raise SystemExit(0)

with Session(engine) as s:
    knowledge_user_id = bind_knowledge_user(s, None)
    print(f"knowledge_user_id={knowledge_user_id}")

    similarity = (1 - KnowledgeChunk.embedding.cosine_distance(query_embedding)).label("similarity")
    stmt = (
        select(KnowledgeChunk.id, KnowledgeChunk.text, similarity)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
        .where(KnowledgeChunk.embedding.isnot(None))
        .where(KnowledgeSource.domain == "health")
        .order_by(similarity.desc())
        .limit(15)
    )
    rows = s.execute(stmt).all()
    print(f"топ-15 по косинусу (порог MIN_COSINE_SIMILARITY=0.35 НЕ применён):")
    for chunk_id, chunk_text, sim in rows:
        preview = chunk_text.replace("\n", " ")[:90]
        flag = "OK " if sim >= 0.35 else "ниже порога"
        print(f"cosine={sim:.4f} [{flag}]  {preview!r}")
PY
