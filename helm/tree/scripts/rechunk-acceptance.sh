#!/usr/bin/env bash
# HELM · что стало с ответами после пересборки поискового слоя. Чтение.
#
# Две вещи, и вторая важнее первой.
#
# 1. Тот самый вопрос, на который 05.09 пришла шапка бланка: «что там
#    прописал врач?». Тогда лексика вернула пять структурно одинаковых
#    строк «Врач: ФИО ______» с одинаковым рангом 0.00760, они заняли
#    весь колчан из пяти, и вектор не запрашивался вовсе.
#
# 2. Структурный ответ про врачей. Чанки этот путь не читает, значит
#    число обязано остаться прежним — 29. Проверяется именно потому, что
#    сегодня я уже один раз сменил слой и обрушил соседний: переключение
#    semantic-v3 оставило личности над прежним поколением, и ответ
#    опустел при целых данных. Замер соседнего слоя после правки — не
#    перестраховка, а следствие той аварии.
#
# Зовутся функции продакшна (`_lexical_search`, `_health_lexical_search`,
# `_vector_search`, `_health_vector_search` из probe.py), а не копии SQL:
# копия с plainto_tsquery в прогоне 307 дала ноль там, где продакшн даёт
# пять, и вывод разбора был на этом построен.
#
# Ничего не пишет: probe() не зовётся, строка прогона ответа не заводится.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Колчан доказательств и структурный ответ после пересборки. Не пишет."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.answer_format import format_doctors, is_quotable
from helm_core.knowledge.embeddings import embed_texts_or_none
from helm_core.knowledge.probe import (MAX_EVIDENCE, _health_lexical_search,
                                       _health_vector_search, _lexical_search,
                                       _vector_search)
from helm_core.knowledge.query_router import answer_doctors_visited
from helm_core.knowledge.tenancy import bind_knowledge_user

QUESTION = "что там прописал врач?"

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

print("############ 1. КОЛЧАН НА ВОПРОС ВЛАДЕЛЬЦА ############")
print(f"  вопрос: {QUESTION}")
lexical = (_lexical_search(session, query=QUESTION, domain=None, knowledge_user_id=tenant)
           + _health_lexical_search(query=QUESTION, knowledge_user_id=tenant))
print(f"\n  --- ЛЕКСИКА: {len(lexical)} ---")
for item in lexical[:MAX_EVIDENCE]:
    mark = "цитируемо" if is_quotable(item.chunk_text) else "ОТБРОШЕНО "
    head = item.chunk_text.replace("\n", " ⏎ ")[:150]
    print(f"    {item.rank:.5f} | {mark} | {len(item.chunk_text):5d} симв | {head}")

if len(lexical) >= MAX_EVIDENCE:
    print("\n  --- ВЕКТОР: не запрашивался, лексика набрала колчан ---")
else:
    embedding = embed_texts_or_none([QUESTION])[0]
    if embedding is None:
        print("\n  --- ВЕКТОР: embed-сервис недоступен ---")
    else:
        seen = {item.chunk_id for item in lexical}
        vector = (_vector_search(session, query_embedding=embedding, domain=None,
                                 knowledge_user_id=tenant, exclude_chunk_ids=seen)
                  + _health_vector_search(query_embedding=embedding,
                                          knowledge_user_id=tenant, exclude_chunk_ids=seen))
        print(f"\n  --- ВЕКТОР: {len(vector)} ---")
        for item in vector[:MAX_EVIDENCE]:
            mark = "цитируемо" if is_quotable(item.chunk_text) else "ОТБРОШЕНО "
            head = item.chunk_text.replace("\n", " ⏎ ")[:150]
            print(f"    {item.rank:.5f} | {mark} | {len(item.chunk_text):5d} симв | {head}")

print("\n############ 2. СОСЕДНИЙ СЛОЙ: СТРУКТУРНЫЙ ОТВЕТ ############")
answer = answer_doctors_visited(session, question="каких врачей я посещал?")
session.rollback()
print(f"  врачей в ответе: {len(answer.items)}  (до пересборки было 29)")
for line in format_doctors(answer).splitlines():
    print(f"    {line}")
if len(answer.items) != 29:
    print("\n  ВНИМАНИЕ: число изменилось. Чанки этот путь не читает —")
    print("  значит либо изменилось что-то ещё, либо предпосылка неверна.")
    raise SystemExit(1)
PYEOF

echo "############ ГОТОВО ############"
