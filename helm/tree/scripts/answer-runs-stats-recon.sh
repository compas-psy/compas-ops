#!/usr/bin/env bash
# HELM · как часто отказ приходит на вопрос, у которого бывал ответ.
#
# ЗАЧЕМ. Три стендовых замера (543, 544, 545) отказ не воспроизвели:
# восемнадцать обращений, ни одного отклонения стражем. Стучаться в
# стенд четвёртый раз бессмысленно — надо смотреть на настоящие вопросы
# владельца, а они лежат в `knowledge_answer_runs` за всё время.
#
# ЧТО ЭТО ПОКАЗЫВАЕТ. Один и тот же вопрос узнаётся по `query_hash`.
# Если у одного хеша есть и N0 (не ответили), и Z0/Z1/Z2 (ответили) —
# это ровно тот дефект, что владелец видит как «отвечает через раз», и
# здесь он посчитан, а не предположен.
#
# ПРИВАТНОСТЬ. Текста вопросов в таблице нет вообще — только хеш.
# Печатаются первые восемь знаков хеша, режимы и числа: восстановить по
# ним формулировку нельзя.
#
# ЧТО ЭТО НЕ ДЕЛАЕТ. Ни одного обращения к модели, ни одной записи:
# только SELECT и откат.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
import collections

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeAnswerRun

session = sessionmaker(bind=create_engine(get_settings().database_url,
                                          pool_pre_ping=True))()
bind_knowledge_user(session, None)

rows = session.execute(
    select(KnowledgeAnswerRun.query_hash, KnowledgeAnswerRun.mode,
           KnowledgeAnswerRun.evidence_count, KnowledgeAnswerRun.paid_ai_used,
           KnowledgeAnswerRun.created_at)
    .order_by(KnowledgeAnswerRun.created_at)).all()

print(f"  строк прогонов всего: {len(rows)}")
if not rows:
    print("  прогонов нет — считать нечего")
    raise SystemExit(0)
print(f"  первый: {rows[0].created_at:%d.%m.%Y}; последний: {rows[-1].created_at:%d.%m.%Y}")
print()

print("############ РЕЖИМЫ ############")
by_mode = collections.Counter(row.mode for row in rows)
for mode, count in by_mode.most_common():
    print(f"  {mode}: {count} ({count / len(rows):.0%})")
print()

print("############ ДВА РАЗНЫХ ОТКАЗА, КОТОРЫЕ СЕЙЧАС СЛИТЫ В ОДИН ############")
refusals = [row for row in rows if row.mode == "N0"]
empty = [row for row in refusals if (row.evidence_count or 0) == 0]
unconfirmed = [row for row in refusals if (row.evidence_count or 0) > 0]
print(f"  N0 всего: {len(refusals)}")
print(f"    «ничего не нашёл» (доказательств 0): {len(empty)}")
print(f"    «нашёл, но не подтвердил» (доказательств больше нуля): {len(unconfirmed)}")
if unconfirmed:
    sizes = collections.Counter(row.evidence_count for row in unconfirmed)
    print(f"    сколько фрагментов было на руках: {dict(sorted(sizes.items()))}")
print()

print("############ ОДИН ВОПРОС — РАЗНЫЕ ИСХОДЫ ############")
by_hash: dict[str, list] = collections.defaultdict(list)
for row in rows:
    by_hash[row.query_hash].append(row)
repeated = {h: items for h, items in by_hash.items() if len(items) > 1}
answered_modes = {"Z0", "Z1", "Z2"}
flapping = {
    h: items for h, items in repeated.items()
    if {row.mode for row in items} & answered_modes and any(row.mode == "N0" for row in items)
}
print(f"  разных вопросов: {len(by_hash)}; задавались повторно: {len(repeated)}")
print(f"  из повторных дали И ответ, И отказ: {len(flapping)}"
      + (f" ({len(flapping) / len(repeated):.0%} повторных)" if repeated else ""))
for h, items in sorted(flapping.items(),
                       key=lambda pair: len(pair[1]), reverse=True)[:8]:
    chain = ", ".join(f"{row.created_at:%d.%m %H:%M} {row.mode}/{row.evidence_count}"
                      for row in items)
    print(f"    {h[:8]}: {chain}")
print()

print("############ ПЛАТНОЕ ############")
paid = [row for row in rows if row.paid_ai_used]
print(f"  прогонов с платной моделью: {len(paid)} из {len(rows)}")
session.rollback()
PYEOF
