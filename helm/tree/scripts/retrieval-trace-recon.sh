#!/bin/bash
# HELM · что РЕАЛЬНО возвращают лексика и вектор на живом вопросе.
#
# action=recon: только чтение; сессия закрывается rollback'ом.
#
# Повод — моя собственная ошибка 05.09.2026. Первый замер (прогон 307)
# воспроизводил лексический поиск SQL-копией с сырым plainto_tsquery и
# получил ноль совпадений, из чего я сделал вывод «лексика не нашла
# ничего, фрагмент поднял вектор». Вывод неверен: продакшн зовёт
# build_or_tsquery() (recall.py:89), которая заменяет ' & ' на ' | ' —
# то есть ищет по ЛЮБОЙ основе вопроса, а не по всем сразу. Копия
# измеряла не тот запрос.
#
# Поэтому здесь копий нет вовсе: дёргаются те же самые функции, что и в
# пути живого ответа, — _lexical_search / _health_lexical_search и
# _vector_search / _health_vector_search из probe.py. Кандидаты обеих
# веток печатаются РАЗДЕЛЬНО, до слияния, с рангом, длиной и вердиктом
# is_quotable, чтобы было видно, кто именно поднял наверх шапку
# документа.
#
# ЦИТАТЫ И ИМЕНА ФАЙЛОВ ПЕЧАТАЮТСЯ — решения владельца 05.09.2026.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Трассировка обеих веток поиска на реальном вопросе владельца."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge import probe as P
from helm_core.knowledge.embeddings import embed_texts_or_none
from helm_core.knowledge.health_schema import health_schema_configured
from helm_core.knowledge.tenancy import bind_knowledge_user

QUESTION = "что там прописал врач?"

# Фильтр цитируемости может быть ещё не выкачен: этот скрипт меряет то,
# что РАБОТАЕТ на сервере, а не то, что лежит в ветке. Импортируется
# по факту; своей копии правила здесь нет намеренно — копия разошлась бы
# с продакшном ровно так же, как разошлась SQL-копия запроса.
try:
    from helm_core.knowledge.answer_format import is_quotable
except ImportError:
    is_quotable = None


def verdict(text):
    if is_quotable is None:
        return "фильтра нет"
    return "цитируемо  " if is_quotable(text) else "ОТБРОШЕНО  "


def show(title, hits):
    print(f"  {title}: {len(hits)}")
    for h in hits:
        rank_ok = "≥порога" if h.rank >= P.MIN_RANK_SCORE else "<порога"
        text = " ".join(h.chunk_text.split())[:90]
        print(f"    {h.rank:.5f} {rank_ok} | {len(h.chunk_text):>4} симв | "
              f"{verdict(h.chunk_text)} | {text}")


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
with sessionmaker(engine, expire_on_commit=False)() as session:
    tenant = bind_knowledge_user(session, None)
    print(f"вопрос: «{QUESTION}»")
    print(f"health-схема настроена: {health_schema_configured()}")
    print("фильтр цитируемости в выкаченном коде: "
          + ("есть" if is_quotable is not None else "ЕЩЁ НЕТ (колонка вердикта пустая)"))
    print(f"MIN_RANK_SCORE={P.MIN_RANK_SCORE} "
          f"MIN_COSINE_SIMILARITY={P.MIN_COSINE_SIMILARITY} "
          f"MIN_LEXICAL_CHUNK_CHARS={P.MIN_LEXICAL_CHUNK_CHARS} "
          f"MAX_EVIDENCE={P.MAX_EVIDENCE}")

    print("\n--- ЛЕКСИКА (build_or_tsquery, как в проде) ---")
    lex = P._lexical_search(session, query=QUESTION, domain=None,
                            knowledge_user_id=tenant)
    show("public", lex)
    hlex = []
    if health_schema_configured():
        hlex = P._health_lexical_search(query=QUESTION, knowledge_user_id=tenant)
        show("health", hlex)

    evidence = sorted((e for e in lex + hlex if e.rank >= P.MIN_RANK_SCORE),
                      key=lambda e: e.rank, reverse=True)[:P.MAX_EVIDENCE]
    print(f"  прошло порог ранга и попало в колчан: {len(evidence)}")

    print("\n--- ВЕКТОР (запрашивается только если колчан неполон) ---")
    if len(evidence) >= P.MAX_EVIDENCE:
        print("  не запрашивался: лексика набрала MAX_EVIDENCE")
    else:
        emb = embed_texts_or_none([QUESTION])[0]
        if emb is None:
            print("  embed-сервис недоступен (fail-open)")
        else:
            exclude = {e.chunk_id for e in evidence}
            vec = P._vector_search(session, query_embedding=emb, domain=None,
                                   knowledge_user_id=tenant, exclude_chunk_ids=exclude)
            show("public", vec)
            hvec = []
            if health_schema_configured():
                hvec = P._health_vector_search(query_embedding=emb,
                                               knowledge_user_id=tenant,
                                               exclude_chunk_ids=exclude)
                show("health", hvec)
            evidence = (evidence + vec + hvec)[:P.MAX_EVIDENCE]

    print("\n--- ИТОГ ---")
    print(f"  колчан до фильтра: {len(evidence)}")
    if evidence:
        print("  что ушло бы владельцу СЕЙЧАС (выкаченный код, без фильтра): "
              + " ".join(evidence[0].chunk_text.split())[:90])
    if is_quotable is None:
        print("  фильтр не выкачен — что он оставит, этот прогон не меряет")
    else:
        kept = [e for e in evidence if is_quotable(e.chunk_text)]
        print(f"  после фильтра:     {len(kept)}")
        if kept:
            print("  первым пойдёт: " + " ".join(kept[0].chunk_text.split())[:90])
        else:
            print("  пусто → outcome=NEEDS_REASONING → вопрос уходит в Hermes "
                  "(платная модель), а НЕ в детерминированный отказ")
    session.rollback()
PYEOF

echo "############ ГОТОВО ############"
