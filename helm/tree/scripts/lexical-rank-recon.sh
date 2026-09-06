#!/usr/bin/env bash
# HELM · чем ранжировать лексику после перечанковки. Чтение.
#
# Прогон 373: лексика даёт 0–1 попадание выше порога на ВОСЬМИ вопросах,
# включая те, ответ на которые в корпусе есть. Причина в связке двух
# чисел: `ts_rank(normalization=2)` делит ранг на длину документа, а
# `MIN_RANK_SCORE=0.003` калибровался на чанках с медианой 65 символов.
# После перечанковки медиана 288 — ранги уехали под порог.
#
# Разбор 05.09 это предсказывал дословно: «Даже с правильными чанками
# ts_rank(normalization=2) продолжит поднимать короткое. Это стоит
# пересмотреть вместе с перечанковкой, а не отдельно». Момент настал.
#
# Меряются три нормализации на одних и тех же вопросах:
#   0 — длину не учитывать вовсе;
#   1 — делить на 1+log(длина): мягкий штраф, не схлопывается на длинных;
#   2 — делить на длину: нынешняя, она и сломалась.
# Запрос строится той же `build_or_tsquery()`, что в проде: копия с
# plainto_tsquery уже однажды дала ноль там, где продакшн давал пять.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Верхний ранг по трём нормализациям. Ничего не пишет."""
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.probe import MIN_RANK_SCORE
from helm_core.knowledge.recall import build_or_tsquery
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import HealthKnowledgeChunk, KnowledgeChunk

RELATED = [
    "какое у меня было давление?",
    "что показал общий анализ крови?",
    "какие были результаты УЗИ брюшной полости?",
    "что написал кардиолог в заключении?",
]
UNRELATED = [
    "у меня зафиксирован квазиперфораторный мнемоглиф?",
    "какой у меня рейтинг в шахматах фиде?",
    "сколько я заплатил за билеты на Марс?",
    "какая марка бетона в фундаменте моего дома?",
]
NORMS = (0, 1, 2)

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)


def top_rank(question: str, norm: int) -> float:
    tsquery = build_or_tsquery(question)
    best = 0.0
    rank = func.ts_rank(KnowledgeChunk.tsv, tsquery, norm)
    row = session.execute(
        select(rank).where(KnowledgeChunk.knowledge_user_id == tenant)
        .order_by(rank.desc()).limit(1)).first()
    if row and row[0] is not None:
        best = max(best, float(row[0]))
    if health_schema_configured():
        with health_session(tenant) as graph:
            hrank = func.ts_rank(HealthKnowledgeChunk.tsv, tsquery, norm)
            hrow = graph.execute(
                select(hrank).where(HealthKnowledgeChunk.knowledge_user_id == tenant)
                .order_by(hrank.desc()).limit(1)).first()
            if hrow and hrow[0] is not None:
                best = max(best, float(hrow[0]))
    return best


print(f"нынешние: normalization=2, MIN_RANK_SCORE={MIN_RANK_SCORE}")
results = {}
for name, questions in (("ответ есть", RELATED), ("ответа нет", UNRELATED)):
    print(f"\n──── {name} ────")
    header = "  " + "".join(f"norm={n:<10}" for n in NORMS) + "вопрос"
    print(header)
    for question in questions:
        row = [top_rank(question, n) for n in NORMS]
        results.setdefault(name, []).append(row)
        cells = "".join(f"{value:<15.5f}" for value in row)
        print(f"  {cells}{question}")

print("\n############ РАЗДЕЛИМОСТЬ ПО НОРМАЛИЗАЦИЯМ ############")
best_choice = None
for index, norm in enumerate(NORMS):
    yes = [row[index] for row in results["ответ есть"]]
    no = [row[index] for row in results["ответа нет"]]
    gap = min(yes) - max(no)
    verdict = "РАЗДЕЛЯЕТ" if gap > 0 else "не разделяет"
    print(f"  norm={norm}: минимум «есть» {min(yes):.5f}, максимум «нет» {max(no):.5f} "
          f"→ {verdict}")
    if gap > 0 and (best_choice is None or gap > best_choice[1]):
        best_choice = (norm, gap, min(yes), max(no))

if best_choice:
    norm, gap, lo, hi = best_choice
    print(f"\n  Лучшее разделение: normalization={norm}, зазор {gap:.5f}.")
    print(f"  Порог имеет смысл ставить между {hi:.5f} и {lo:.5f};")
    print(f"  середина — {(hi + lo) / 2:.5f}.")
else:
    print("\n  Ни одна нормализация не разделяет группы. Тогда дело не в")
    print("  ранжировании, и порог трогать нельзя — надо смотреть выше.")

session.rollback()
PYEOF
rc=$?

if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi

echo "############ ГОТОВО ############"
