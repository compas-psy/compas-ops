#!/usr/bin/env bash
# HELM · вопрос с периодом больше не отвечается молча. Чтение.
#
# Пункт аудита владельца 06.09.2026: «нераспознанное временное
# ограничение не должно молчаливо игнорироваться». До правки «каких
# врачей я посещал в марте 2025» отбиралось по ВСЕМУ 2025 году, а «за
# последний год» — вообще без отбора, и ответ выглядел ответом на
# заданный вопрос.
#
# Проверяется на живом корпусе то же, что юнит-тестами на синтетике, но
# настоящим исполнителем и настоящими данными. Зовётся
# `answer_doctors_visited` напрямую, а не `probe()`: probe заводит
# строку прогона ответа, а recon по контракту ничего не пишет.
#
# Печатается текст ответа. Имена врачей в нём есть — это данные
# владельца, и он 06.09.2026 разрешил их вывод в лог Actions.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Ответ на четыре вопроса про время. Ничего не пишет."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.answer_format import format_doctors
from helm_core.knowledge.query_router import answer_doctors_visited

QUESTIONS = [
    ("без периода", "каких врачей я посещал?"),
    ("год — применяется", "каких врачей я посещал в 2025 году?"),
    ("месяц — применить нечем", "каких врачей я посещал в марте 2025?"),
    ("относительный период", "каких врачей я посещал за последний год?"),
]

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()

failures = []
for name, question in QUESTIONS:
    answer = answer_doctors_visited(session, question=question)
    text = format_doctors(answer)
    session.rollback()
    print(f"\n──── {name} ────")
    print(f"  вопрос:   {question}")
    print(f"  год:      {answer.year}")
    print(f"  период:   {answer.unsupported_period}")
    print("  ответ:")
    for line in text.splitlines():
        print(f"    {line}")

    named = answer.unsupported_period is not None
    if named and answer.unsupported_period not in text:
        failures.append(f"{name}: период не назван в тексте ответа")
    if not named and "не умею" in text:
        failures.append(f"{name}: сказано про период там, где периода нет")

print("\n############ ИТОГ ############")
if failures:
    for line in failures:
        print(f"  ПРОВАЛ: {line}")
    raise SystemExit(1)
print("  Период, который отбор применить не смог, назван в ответе.")
print("  Вопрос без периода лишнего не говорит.")
PYEOF
rc=$?

# Код возврата Python обязан дойти до workflow. Без этой проверки
# «ГОТОВО» печаталось и после упавшей проверки, а прогон оставался
# зелёным: так прошёл прогон 354 с AttributeError, и так прошёл бы
# любой провалившийся гейт ниже. Найдено аудитом владельца 06.09.2026;
# сам workflow код возврата пробрасывает верно, глушил его скрипт.
if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi

echo "############ ГОТОВО ############"
