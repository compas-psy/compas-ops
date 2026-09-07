#!/usr/bin/env bash
# HELM · доходит ли записанное владельцем до пятёрки фрагментов. Чтение.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from datetime import date

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.answer_format import is_quotable
from helm_core.knowledge.embeddings import embed_texts_or_none
from helm_core.knowledge.probe import (MAX_EVIDENCE, _lexical_search, _health_lexical_search,
                                       _vector_search)
from helm_core.knowledge.query_spec import build_query_spec
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeSource

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

print("############ ЭМБЕДДИНГИ СЕГОДНЯШНИХ ЗАПИСЕЙ ############")
rows = session.execute(
    select(KnowledgeSource.original_filename,
           func.count(KnowledgeChunk.id),
           func.count(KnowledgeChunk.embedding))
    .join(KnowledgeChunk, KnowledgeChunk.source_id == KnowledgeSource.id)
    .where(KnowledgeSource.created_at >= date.today())
    .group_by(KnowledgeSource.original_filename)
).all()
for name, chunks, embedded in rows:
    print(f"  чанков {chunks}, с эмбеддингом {embedded} · {(name or '')[:45]}")

QUESTIONS = [
    "До какого числа у меня действует загран?",
    "Что я беру с собой из лекарств?",
    "Кто у меня страховщик?",
    "В каком порядке я всё делаю по прилёте?",
]
print("############ ОТКУДА БЕРЁТСЯ ПЯТЁРКА ############")
for q in QUESTIONS:
    spec = build_query_spec(q, tenant_id=tenant)
    lex = _lexical_search(session, query=spec.retrieval_question, domain=None,
                          knowledge_user_id=tenant)
    lex += _health_lexical_search(query=spec.retrieval_question, knowledge_user_id=tenant)
    lex.sort(key=lambda e: e.rank, reverse=True)
    ok = [e for e in lex if is_quotable(e.chunk_text)]
    print(f"\n  {q}")
    print(f"    режим={spec.mode} лексики={len(lex)} пригодных={len(ok)} "
          f"вектор {'НЕ ЗАПРОСИТСЯ' if len(ok) >= MAX_EVIDENCE else 'запросится'}")
    for e in ok[:MAX_EVIDENCE]:
        print(f"      {e.rank:.5f} {(e.original_filename or e.chunk_text)[:52]}")
    emb = embed_texts_or_none([spec.retrieval_question])[0]
    if emb is None:
        print("      ВЕКТОР: embed-сервис недоступен")
        continue
    vec = _vector_search(session, query_embedding=emb, domain=None,
                         knowledge_user_id=tenant, exclude_chunk_ids=set())
    print(f"      что дал бы вектор ({len(vec)}):")
    for e in vec[:3]:
        print(f"        {e.rank:.3f} {(e.original_filename or e.chunk_text)[:52]}")
session.rollback()
PYEOF
rc=$?
if [ "$rc" -ne 0 ]; then echo "############ ПРОВАЛ (код $rc) ############"; exit "$rc"; fi
echo "############ ГОТОВО ############"
