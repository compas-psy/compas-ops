#!/bin/bash
# Диагностика: почему "каких врачей я посещал" не находит чанки со
# специальностями (гастроэнтеролог, офтальмолог и т.д.), если только
# в них нет буквального слова "врач". Проверяет: (1) есть ли вообще
# embedding у health-чанков (эмбеддинг-сервис мог быть недоступен при
# разборе — fail-open тихо оставляет embedding=NULL), (2) если есть —
# какой реальный косинус даёт "врачи" против чанка со специальностью,
# и как это соотносится с порогом MIN_COSINE_SIMILARITY=0.35.
#
# health.* схема (ADR-005/P12) на этом сервере ещё НЕ создана
# (scripts/setup-health-role.sh не прогнан) — health-чанки пока в
# public.knowledge_chunks (domain='health'), запрос идёт туда.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

echo '=== покрытие embedding у health-чанков (public, до выката P12) ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select count(*) filter (where c.embedding is not null) as with_embedding,
          count(*) as total
   from knowledge_chunks c
   join knowledge_sources s on s.id = c.source_id
   where s.domain = 'health'"

echo '=== примеры чанков со специальностями (первые 200 символов) ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select c.id, (c.embedding is not null) as has_embedding, left(c.text, 200) as text
   from knowledge_chunks c
   join knowledge_sources s on s.id = c.source_id
   where s.domain = 'health'
     and c.text ~* 'гастроэнтеролог|офтальмолог|невролог|уролог|кардиолог|дерматолог'
   limit 10"

echo '=== реальный косинус "врачи" против этих чанков ==='
cd /opt/helm/compose
sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine, select

from helm_core.config import get_settings
from helm_core.knowledge.embeddings import embed_texts_or_none
from helm_core.models import KnowledgeChunk, KnowledgeSource
from sqlalchemy.orm import Session

engine = create_engine(get_settings().database_url)

query_embedding = embed_texts_or_none(["каких врачей я посещал"])[0]
if query_embedding is None:
    print("embed-сервис недоступен ПРЯМО СЕЙЧАС — embed_texts_or_none вернул None")
    raise SystemExit(0)

with Session(engine) as s:
    rows = s.execute(
        select(KnowledgeChunk.id, KnowledgeChunk.text, KnowledgeChunk.embedding)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
        .where(KnowledgeSource.domain == "health")
        .where(KnowledgeChunk.text.op("~*")(
            "гастроэнтеролог|офтальмолог|невролог|уролог|кардиолог|дерматолог"))
        .limit(10)
    ).all()
    if not rows:
        print("ни один чанк не содержит названия специальности вообще")
    for chunk_id, chunk_text, embedding in rows:
        if embedding is None:
            print(f"{chunk_id}: embedding=NULL, текст: {chunk_text[:100]!r}")
            continue
        dot = sum(x * y for x, y in zip(query_embedding, embedding))
        norm_a = sum(x * x for x in query_embedding) ** 0.5
        norm_b = sum(y * y for y in embedding) ** 0.5
        cos = dot / (norm_a * norm_b)
        print(f"{chunk_id}: cosine={cos:.4f} (порог 0.35), текст: {chunk_text[:100]!r}")
PY
