#!/usr/bin/env bash
# HELM · что изменилось в ответах после переразбора книги. Только чтение.
#
# ЭТО НЕ ПРОВЕРКА БОТА. Здесь вызывается `probe()` напрямую, минуя
# Telegram и плагин: видно поиск и исполнение, но не путь сообщения.
# Приёмка через бота — отдельная работа владельца.
#
# Три вопроса, каждый со своей причиной:
#   1. «Сколько у меня каналов?» — регрессия прогона 427. Книга одним
#      фрагментом совпадала со всем подряд и дала «Насчитал 6» цитатой
#      из книги. Ожидается честное «не нашёл» либо ответ по записи о
#      каналах, но НЕ из книги;
#   2. вопрос по самой книге — она теперь должна отвечать разделом, а
#      не всем текстом сразу;
#   3. вопрос по здоровью — проверка, что переразбор ничего не сломал в
#      том, что уже работало.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.probe import probe
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
tenant = bind_knowledge_user(session, None)

QUESTIONS = [
    "Сколько у меня каналов?",
    "Что такое эмоционально-образная терапия?",
    "Чем психотерапия отличается от консультирования?",
    "Какой у меня был холестерин в последний раз?",
]

for question in QUESTIONS:
    print(f"############ {question} ############")
    try:
        answer = probe(session, query=question, knowledge_user_id=tenant)
    except Exception as exc:  # noqa: BLE001 — диагностика, не продакшн-путь
        print(f"  ИСКЛЮЧЕНИЕ: {type(exc).__name__}: {exc}")
        continue
    print(f"  исход: {answer.outcome} | режим: {answer.mode}")
    print(f"  ответ: {answer.answer_text}")
    print(f"  кандидатов: {len(answer.candidates or [])}")
    for evidence in (answer.evidence or [])[:3]:
        print(f"    источник: {evidence.original_filename} | {evidence.chunk_text[:120]!r}")
    print()
PYEOF
