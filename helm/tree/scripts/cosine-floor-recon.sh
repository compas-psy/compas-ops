#!/usr/bin/env bash
# HELM · где проходит граница «похоже» и «не про это». Чтение.
#
# Прогон 371: на вопрос «у меня зафиксирован квазиперфораторный
# мнемоглиф?» — слова выдуманы, лексических совпадений ноль — векторная
# ветка вернула УЗИ почек, и ответ вышел «ближайшее из ваших записей».
# Порог MIN_COSINE_SIMILARITY=0.35 его пропустил.
#
# Комментарий у самой константы это предсказывал: «тот же статус, что
# MIN_RANK_SCORE до своего первого реального использования», то есть
# значение выбрано до калибровки. Здесь калибровка и делается: один
# замер, две группы вопросов, решение по разделимости.
#
# Следствие важнее числа: пока порог пропускает что угодно, ветка
# «честного отсутствия сведений» недостижима в принципе — на любой
# вопрос найдётся ближайший сосед.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Максимальная косинусная близость по двум группам вопросов. Не пишет."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.embeddings import embed_texts_or_none
from helm_core.knowledge.probe import (MIN_COSINE_SIMILARITY, _health_vector_search,
                                       _vector_search)
from helm_core.knowledge.tenancy import bind_knowledge_user

# Про что в корпусе заведомо есть.
RELATED = [
    "какое у меня было давление?",
    "что показал общий анализ крови?",
    "какие были результаты УЗИ брюшной полости?",
    "что написал кардиолог в заключении?",
]
# Про что заведомо нет: выдуманные слова и чужие темы.
UNRELATED = [
    "у меня зафиксирован квазиперфораторный мнемоглиф?",
    "какой у меня рейтинг в шахматах фиде?",
    "сколько я заплатил за билеты на Марс?",
    "какая марка бетона в фундаменте моего дома?",
]

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)


def top_similarity(question: str) -> float | None:
    embedding = embed_texts_or_none([question])[0]
    if embedding is None:
        return None
    hits = (_vector_search(session, query_embedding=embedding, domain=None,
                           knowledge_user_id=tenant, exclude_chunk_ids=set())
            + _health_vector_search(query_embedding=embedding,
                                    knowledge_user_id=tenant, exclude_chunk_ids=set()))
    return max((hit.rank for hit in hits), default=None)


def measure(name, questions):
    print(f"\n──── {name} ────")
    values = []
    for question in questions:
        top = top_similarity(question)
        if top is None:
            print(f"  (embed недоступен) | {question}")
            continue
        values.append(top)
        print(f"  {top:.3f} | {question}")
    return values


print(f"нынешний порог MIN_COSINE_SIMILARITY = {MIN_COSINE_SIMILARITY}")
print("ВНИМАНИЕ: `_vector_search` уже отсекает по этому порогу, поэтому")
print("значения ниже него сюда не попадают и печатаются как «нет находок».")

related = measure("вопросы, ответ на которые в корпусе есть", RELATED)
unrelated = measure("вопросы не про этот корпус", UNRELATED)

print("\n############ РАЗДЕЛИМОСТЬ ############")
if related and unrelated:
    print(f"  минимум по «есть»:   {min(related):.3f}")
    print(f"  максимум по «нет»:   {max(unrelated):.3f}")
    if min(related) > max(unrelated):
        print(f"  РАЗДЕЛЯЮТСЯ. Порог имеет смысл ставить между "
              f"{max(unrelated):.3f} и {min(related):.3f}.")
    else:
        print("  НЕ РАЗДЕЛЯЮТСЯ одним числом: связанные и несвязанные вопросы")
        print("  перекрываются по близости. Поднимать порог значит терять")
        print("  настоящие ответы, оставлять — отвечать чем попало.")
elif not unrelated:
    print("  Ни один несвязанный вопрос не прошёл нынешний порог — значит")
    print("  0.35 их уже отсекает, и дело не в пороге. Смотреть надо на")
    print("  лексическую ветку.")
else:
    print("  Нет данных по одной из групп.")
session.rollback()
PYEOF
rc=$?

if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi

echo "############ ГОТОВО ############"
