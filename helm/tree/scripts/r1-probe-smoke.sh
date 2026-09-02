#!/bin/bash
# HELM v4.0 RESCUE · R1: живая проверка, что после переноса поиск отвечает
# из health-схемы, а не погас.
#
# Смысл именно в этом: health_schema_configured() читается один раз при
# старте процесса, а probe() после R1 намеренно НЕ ищет health в общей
# схеме. Если контейнер не увидел DSN, ответом будет тишина без ошибки —
# ровно тот отказ, который нельзя заметить по логам.
#
# Транзакция откатывается: probe() пишет строку в knowledge_answer_runs,
# и засорять ею статистику владельца из-за проверки незачем.
set -uo pipefail
cd /opt/helm/compose || exit 1

sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured
from helm_core.knowledge.probe import probe

print("health_schema_configured() =", health_schema_configured())

QUESTIONS = [
    "каких врачей я посещал",
    "какие анализы я сдавал",
    "что там с щитовидной железой",
]

engine = create_engine(get_settings().database_url)
with Session(engine) as s:
    for question in QUESTIONS:
        result = probe(s, query=question)
        texts = [e.chunk_text for e in result.evidence]
        print(f"\n=== {question}")
        print(f"    исход: {result.outcome}, режим: {result.mode}, "
              f"доказательств: {len(texts)}, из них уникальных: {len(set(texts))}")
        for e in result.evidence[:3]:
            source = e.original_filename or e.source_id
            print(f"    · {e.chunk_text[:110].replace(chr(10), ' ')}   [{source}]")
    s.rollback()
PY
