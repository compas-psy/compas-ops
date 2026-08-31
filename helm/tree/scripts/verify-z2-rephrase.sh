#!/bin/bash
# Живая проверка E12 фазы 2 (Z2-рефраз gemma2:2b + личный стиль владельца)
# после выката. Тот же факт/вопрос, что в живом замере моделей
# (docs/KNOWLEDGE_MODELS.md, «Живой замер 31.08.2026») — здесь проверяется
# не сама модель, а её реальное вписывание в probe(): что Z0-ответ
# приходит СТИЛИЗОВАННЫМ, а не сырой цитатой, когда Ollama доступна.
# Транзакция откатывается — тестовые данные не остаются в базе.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail
cd /opt/helm/compose || exit 1

echo '=== Z2-рефраз: Z0-ответ через probe() со стилем владельца ==='
sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.probe import probe

engine = create_engine(get_settings().database_url)
with Session(engine) as s:
    ingest_text(
        s, domain="psychology",
        text="Схема — это устойчивый паттерн мышления и поведения, сформированный в детстве.",
        original_filename="test-z2-verify.txt",
    )
    s.flush()
    result = probe(s, query="что такое схема?")
    print("outcome:", result.outcome)
    print("mode:", result.mode)
    print("answer_text:")
    print(result.answer_text)
    s.rollback()
    print("транзакция откачена — тестовые данные не остались в базе")
PY

echo
echo '=== ollama: резидентная память после вызова (KEEP_ALIVE=0 должен выгрузить веса) ==='
sudo docker stats --no-stream --format '{{.Name}}: {{.MemUsage}}' "$(sudo docker compose ps -q ollama)"
