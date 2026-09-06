#!/usr/bin/env bash
# HELM · вопрос о своих данных не оплачивается никогда. ЗАПИСЬ (журнал).
#
# Распоряжение владельца 06.09.2026: «Отсутствие находок не является моим
# разрешением оплатить ответ». До правки платный переход блокировался
# только при LOCAL_UNAVAILABLE, то есть при СБОЕ; пустой поиск по личному
# вопросу давал NEEDS_REASONING и уходил в платную модель.
#
# ЧТО СЧИТАЕТСЯ ДОКАЗАТЕЛЬСТВОМ. Не `paid_ai_used=false`: этот флаг
# говорит, что строку записали бесплатной, и ничего не говорит о том,
# состоялся ли вызов. Здесь проверяются две вещи, которых флаг не
# заменяет:
#   исход probe, потому что именно он лишает вызывающего права на
#   эскалацию (LOCAL_NOT_FOUND против NEEDS_REASONING);
#   счётчик исходящих соединений к платной модели через
#   sing-box@openrouter-proxy до и после — если он вырос, значит вызов
#   всё-таки был, чем бы ни была записана строка.
#
# probe заводит строку в knowledge_answer_runs, поэтому maintenance.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

# ── исходящие к платной модели ДО проверки ───────────────────────────
COUNT_BEFORE=$(sudo journalctl -u sing-box@openrouter-proxy --since "-10 min" --no-pager 2>/dev/null \
               | grep -ci "openrouter" || true)
echo "исходящих к openrouter за последние 10 минут ДО: $COUNT_BEFORE"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Исходы probe на трёх сценариях. Заводит строки журнала."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge import probe as probe_mod
from helm_core.knowledge.probe import probe
from helm_core.knowledge.query_scope import is_personal_data_question
from helm_core.models import KnowledgeAnswerRun

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
Session = sessionmaker(bind=engine)

# Вопрос о данных владельца, у которого НЕТ лексических пересечений с
# корпусом. Прогон 370 показал, почему это существенно: «какой у меня
# был уровень криптонита в крови» дал Z1 «ближайшее из ваших записей»
# по основам «уровень» и «кров», то есть до ветки пустого поиска не
# дошёл вовсе. Слова ниже подобраны так, чтобы не совпасть ни одной
# основой; проверяется ветка, а не корпус.
NO_SUCH = "у меня зафиксирован квазиперфораторный мнемоглиф?"
GENERAL = "переведи на английский слово «квазиперфораторный»"

failures = []


def run(name, question, expect_outcome, *, break_local=False):
    session = Session()
    if break_local:
        # Обе ветки поиска валятся: имитируем отказ локального пути
        # внутри probe, а не снаружи. Плагин снаружи отдаёт при этом
        # LOCAL_UNAVAILABLE, и это покрыто юнит-тестами; здесь нужно
        # убедиться, что сам probe не превращает свой сбой в разрешение
        # заплатить.
        def boom(*a, **kw):
            raise TimeoutError("локальный путь недоступен")
        probe_mod._lexical_search = boom
        probe_mod._health_lexical_search = boom
    try:
        result = probe(session, query=question)
        outcome = result.outcome
        answer = (result.answer_text or "")[:120]
        error = None
    except Exception as exc:
        outcome = f"ИСКЛЮЧЕНИЕ:{type(exc).__name__}"
        answer = ""
        error = exc
    session.commit()
    session.close()

    print(f"\n──── {name} ────")
    print(f"  вопрос:  {question}")
    print(f"  личный:  {is_personal_data_question(question)}")
    print(f"  исход:   {outcome}")
    if answer:
        print(f"  ответ:   {answer}")
    if outcome != expect_outcome:
        failures.append(f"{name}: ждали {expect_outcome}, получили {outcome}")
    return error


run("пустой поиск, вопрос о своих данных", NO_SUCH, "LOCAL_NOT_FOUND")
run("общий вопрос", GENERAL, "NEEDS_REASONING")
# Сбой локального пути проверяется последним: он ломает модуль в этом
# процессе, и после него остальные сценарии недостоверны.
err = run("сбой локального пути", NO_SUCH, "ИСКЛЮЧЕНИЕ:TimeoutError",
          break_local=True)
if err is None:
    failures.append("сбой локального пути: probe не пробросил исключение наружу, "
                    "вызывающий не отличит сбой от «ответа нет»")

# ── наблюдение, не гейт ──────────────────────────────────────────────
# Прогон 370 показал: на личный вопрос, слова которого в корпусе есть,
# система отвечает Z1 «ближайшее из ваших записей», а не «не нашёл».
# Денег это не стоит, но и ответом не является. Печатается, чтобы
# ветка LOCAL_NOT_FOUND не выглядела частым случаем, каким она не
# является.
session = Session()
observed = probe(session, query="какой у меня был уровень криптонита в крови?")
session.commit()
session.close()
print("\n──── наблюдение: личный вопрос со знакомыми словами ────")
print(f"  исход: {observed.outcome}  режим: {observed.mode}")
print(f"  ответ: {(observed.answer_text or '')[:100]}")

# ── журнал ───────────────────────────────────────────────────────────
session = Session()
rows = session.execute(
    select(KnowledgeAnswerRun.mode, KnowledgeAnswerRun.paid_ai_used,
           KnowledgeAnswerRun.evidence_count)
    .order_by(KnowledgeAnswerRun.created_at.desc()).limit(3)).all()
session.rollback()
session.close()
print("\n──── последние строки журнала ────")
for mode, paid, count in rows:
    print(f"  mode={mode} paid_ai_used={paid} evidence_count={count}")
if not any(mode == "N0" and paid is False for mode, paid, _ in rows):
    failures.append("строки mode=N0 с paid_ai_used=false в журнале нет")

print("\n############ ИТОГ ############")
if failures:
    for line in failures:
        print(f"  ПРОВАЛ: {line}")
    raise SystemExit(1)
print("  Пустой поиск по личному вопросу не даёт права на оплату.")
print("  Общий вопрос по-прежнему доходит до платной модели.")
print("  Сбой локального пути пробрасывается вызывающему, а не глотается.")
PYEOF
rc=$?

# ── исходящие к платной модели ПОСЛЕ проверки ────────────────────────
COUNT_AFTER=$(sudo journalctl -u sing-box@openrouter-proxy --since "-10 min" --no-pager 2>/dev/null \
              | grep -ci "openrouter" || true)
echo
echo "исходящих к openrouter за последние 10 минут ПОСЛЕ: $COUNT_AFTER"
if [ "$COUNT_AFTER" -gt "$COUNT_BEFORE" ]; then
  echo "  ПРОВАЛ: во время проверки был выход к платной модели ($COUNT_BEFORE → $COUNT_AFTER)"
  rc=1
elif [ "$COUNT_BEFORE" -eq 0 ] && [ "$COUNT_AFTER" -eq 0 ]; then
  echo "  выходов к платной модели не было"
  echo "  ОГОВОРКА: нулевой счётчик и до, и после может означать, что этот"
  echo "  журнал вообще не пишет таких строк. Тогда наблюдение не состоялось,"
  echo "  и вес имеет только исход probe выше."
fi

if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi

echo "############ ГОТОВО ############"
