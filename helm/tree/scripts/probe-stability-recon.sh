#!/usr/bin/env bash
# HELM · плавает ли сам набор фрагментов, который поиск отдаёт синтезу.
#
# ЗАЧЕМ. Разведка детерминизма синтеза (прогон 543) сняла подозрение с
# генерации: пять вызовов подряд дали три разных формулировки, и НИ ОДНА
# не была отклонена стражем. Значит расхождение исхода в прогоне 541
# объясняется не сэмплированием.
#
# Зато та же разведка показала «фрагментов на входе синтеза: 1», тогда
# как отказ в 541 перечислил пять документов, ответ в 542 — четыре, а в
# 539 — два. То есть меняется ВХОД синтеза, а не только его выход. При
# одном фрагменте задача тривиальна — перенести число; при пяти надо
# выбрать между бланками разных лет, и там страж срабатывает.
#
# ЧТО ЭТО МЕРЯЕТ. Шесть вызовов `probe()` на один и тот же вопрос:
# исход, режим, сколько фрагментов дошло до синтеза, сколько всего
# нашлось, и какие документы. При отказе печатается причина от стража —
# его собственным текстом, не пересказом.
#
# ЧТО ЭТО НЕ ДЕЛАЕТ. Не чинит и не пишет: сессия откатывается после
# каждого вызова, строки прогонов не остаются. Тексты ответов не
# печатаются — печатается форма исхода.
set -uo pipefail
cd /opt/helm/compose || exit 1

QUESTION="${1:-какой у меня был гемоглобин}"

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - "$QUESTION" <<'PYEOF'
import collections
import logging
import sys
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.probe import probe
from helm_core.knowledge.tenancy import bind_knowledge_user

QUESTION = sys.argv[1]
ATTEMPTS = 6

reasons: list[str] = []


class Collect(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        reasons.append(record.getMessage())


for name in ("helm_core.knowledge.synthesis", "helm_core.knowledge.probe"):
    log = logging.getLogger(name)
    log.addHandler(Collect())
    log.setLevel(logging.WARNING)

session = sessionmaker(bind=create_engine(get_settings().database_url,
                                          pool_pre_ping=True))()
bind_knowledge_user(session, None)

print(f"  вопрос: {QUESTION}")
print(f"  заходов: {ATTEMPTS}")
print()

shapes: list[tuple[str, int]] = []
for number in range(1, ATTEMPTS + 1):
    reasons.clear()
    started = time.monotonic()
    result = probe(session, query=QUESTION)
    spent = time.monotonic() - started
    session.rollback()

    names = [item.original_filename or item.source_id for item in result.evidence]
    shapes.append((result.outcome, len(result.evidence)))
    print(f"  заход {number}: {result.outcome}, режим {result.mode}, "
          f"фрагментов синтезу {len(result.evidence)}, найдено всего "
          f"{len(result.candidates)}  [{spent:.0f} с]")
    if names:
        print(f"    документы: {'; '.join(names)}")
    for reason in reasons:
        print(f"    причина: {reason}")

print()
print("############ ИТОГ ############")
by_outcome = collections.Counter(outcome for outcome, _ in shapes)
by_size = collections.Counter(size for _, size in shapes)
print(f"  исходы: {dict(by_outcome)}")
print(f"  фрагментов синтезу: {dict(by_size)}")
pairs = collections.Counter(shapes)
print(f"  пары (исход, фрагментов): {dict(pairs)}")
PYEOF
