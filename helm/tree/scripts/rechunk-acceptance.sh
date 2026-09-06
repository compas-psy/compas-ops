#!/usr/bin/env bash
# HELM · что отвечает память после пересборки. ЗАПИСЬ (одна строка).
#
# Зовётся настоящий `probe()` — тот самый код, через который проходит
# вопрос из Telegram, вместе с порядком веток, композитором и рефразом.
# Он заводит строку в `knowledge_answer_runs` (счётчик ухода от платной
# модели, §14.14), поэтому действие `maintenance`, а не `recon`: recon по
# контракту не пишет, и обходить это молча нельзя.
#
# Мерить копией порядка веток я не стал сознательно: копия с
# plainto_tsquery в прогоне 307 дала ноль там, где продакшн давал пять, и
# на этом был построен неверный вывод.
#
# Три вопроса:
#   1. «что там прописал врач?» — тот, на который 05.09 пришла шапка
#      бланка, а 06.09 (прогон 365) колчан заняли пять подписей и
#      векторная ветка не запросилась.
#   2. «каких врачей я посещал?» — соседний слой, чанков не читает,
#      обязан остаться прежним (29). Проверяется потому, что сегодня я
#      уже сменил один слой и обрушил соседний.
#   3. «какое у меня было давление?» — обычный фактический вопрос, на
#      котором видно, портит ли новая нарезка нормальный случай.
#
# В лог попадают имена врачей и значения из документов владельца: он
# разрешил это 06.09.2026.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Живые ответы памяти через продакшн-путь. Пишет строку прогона."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.probe import probe

QUESTIONS = [
    ("тот самый вопрос", "что там прописал врач?"),
    ("соседний слой", "каких врачей я посещал?"),
    ("обычный факт", "какое у меня было давление?"),
]

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
Session = sessionmaker(bind=engine)

doctors_count = None
for name, question in QUESTIONS:
    session = Session()
    result = probe(session, query=question)
    session.commit()
    session.close()
    print(f"\n──── {name} ────")
    print(f"  вопрос:   {question}")
    print(f"  исход:    {result.outcome}  режим: {result.mode}")
    print(f"  источников: {len(result.sources)}")
    for source in result.sources[:6]:
        print(f"    {source}")
    print("  ответ:")
    for line in (result.answer_text or "(эскалация к платной модели)").splitlines():
        print(f"    {line}")
    if name == "соседний слой":
        doctors_count = result.answer_text

print("\n############ ИТОГ ############")
if doctors_count and "29 врачей" in doctors_count:
    print("  Соседний слой цел: те же 29 врачей.")
else:
    print("  ВНИМАНИЕ: структурный ответ изменился. Чанки этот путь не читает,")
    print("  значит либо сломано что-то ещё, либо предпосылка неверна.")
    raise SystemExit(1)
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
