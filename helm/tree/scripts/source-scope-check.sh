#!/usr/bin/env bash
# HELM · ограничение поиска источником, названным в вопросе. Чтение.
#
# ЭТО НЕ ПРОВЕРКА БОТА: `probe()` зовётся напрямую, минуя Telegram и
# плагин. Видно поиск и исполнение, не путь сообщения.
#
# Вопросы 1–3 — дословно из прогона владельца 18:19–18:21, где «по книге
# Линде» игнорировалось и ответом шла случайная глава той же книги.
# Вопрос 4 — несуществующий источник: ответ из другого документа здесь
# хуже отказа. Вопрос 5 — контроль, что вопрос без имени источника
# работает как прежде.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.probe import probe
from helm_core.knowledge.query_spec import detect_source_hint
from helm_core.knowledge.tenancy import bind_knowledge_user

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
tenant = bind_knowledge_user(session, None)

QUESTIONS = [
    "По книге Линде что такое ЭОТ?",
    "По книге Линде скажи, что такое эмоционально-образная терапия",
    "По книге Линде дай примеры того, как бороться с неуверенностью",
    "По книге Фрейда что сказано о зависти?",
    "Какой у меня был холестерин в последний раз?",
]

for question in QUESTIONS:
    print(f"############ {question} ############")
    print(f"  название источника из вопроса: {detect_source_hint(question)!r}")
    try:
        answer = probe(session, query=question, knowledge_user_id=tenant)
    except Exception as exc:  # noqa: BLE001 — диагностика, не продакшн-путь
        print(f"  ИСКЛЮЧЕНИЕ: {type(exc).__name__}: {exc}")
        continue
    print(f"  исход: {answer.outcome} | режим: {answer.mode} | кандидатов: {len(answer.candidates or [])}")
    print(f"  ответ: {(answer.answer_text or '')[:400]}")
    for evidence in (answer.evidence or [])[:2]:
        print(f"    источник: {evidence.original_filename} | {evidence.chunk_text[:110]!r}")
    print()
PYEOF
