#!/bin/bash
# Диагностика: почему "каких врачей я посещал" не находит чанки со
# специальностями (гастроэнтеролог, офтальмолог и т.д.), если только
# в них нет буквального слова "врач". Проверяет: (1) есть ли вообще
# embedding у health-чанков (эмбеддинг-сервис мог быть недоступен при
# разборе — fail-open тихо оставляет embedding=NULL), (2) если есть —
# какой реальный косинус даёт "врачи" против чанка со специальностью,
# и как это соотносится с порогом MIN_COSINE_SIMILARITY=0.35.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

echo '=== покрытие embedding у health-чанков ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select count(*) filter (where embedding is not null) as with_embedding,
          count(*) as total
   from health.knowledge_chunks"

echo '=== примеры чанков со специальностями (первые 200 символов) ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select id, (embedding is not null) as has_embedding, left(text, 200) as text
   from health.knowledge_chunks
   where text ~* 'гастроэнтеролог|офтальмолог|невролог|уролог|кардиолог|дерматолог'
   limit 10"

echo '=== реальный косинус "врачи" против этих чанков ==='
cd /opt/helm/compose
sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.embeddings import embed_texts_or_none
from helm_core.knowledge.health_schema import health_session
from helm_core.models import HealthKnowledgeChunk

engine = create_engine(get_settings().database_url)
with Session(engine) as s:
    row = s.execute(text(
        "select knowledge_user_id from health.knowledge_chunks limit 1"
    )).first()
    if row is None:
        print("нет health-чанков вообще")
        raise SystemExit(0)
    tenant_id = row[0]

query_embedding = embed_texts_or_none(["каких врачей я посещал"])[0]
if query_embedding is None:
    print("embed-сервис недоступен ПРЯМО СЕЙЧАС — embed_texts_or_none вернул None")
    raise SystemExit(0)

with health_session(tenant_id) as hs:
    rows = hs.execute(
        select(HealthKnowledgeChunk.id, HealthKnowledgeChunk.text, HealthKnowledgeChunk.embedding)
        .where(HealthKnowledgeChunk.text.op("~*")(
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
