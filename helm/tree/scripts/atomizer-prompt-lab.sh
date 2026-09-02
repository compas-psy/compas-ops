#!/bin/bash
# Стенд подбора промпта атомизатора (ADR-019 фаза 4).
#
# ЗАЧЕМ ОТДЕЛЬНО ОТ atomizer.py: правка промпта в коде требует полного
# деплоя (~22 минуты из-за точки возврата перед выкатом), а recon-скрипт
# доставляется на сервер scp при каждом запуске — цикл "правка → факт"
# сокращается до минуты. Поэтому здесь промпты заданы ЛОКАЛЬНО, а не
# импортируются из atomizer.py: скрипт намеренно не зависит от того,
# какая версия кода сейчас на сервере.
#
# Печатает СЫРОЙ ответ модели целиком по каждому варианту промпта — без
# разбора и фильтрации. Ничего никуда не пишет: только читает реальный
# health-текст и зовёт локальную Ollama.
#
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

cd /opt/helm/compose
sudo docker compose exec -T helm-core python3 <<'PY'
import json
import urllib.request

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeSource

OLLAMA_URL = "http://ollama:11434/api/generate"
MODEL = "gemma2:2b"

# Вариант A — то, что сейчас в коде (после правки 02.09).
PROMPT_A = (
    "Ты — детерминированный извлекатель структурированных знаний из личного "
    "архива владельца (любая сфера жизни: здоровье, работа, покупки, "
    "обучение, встречи, проекты). Разбей текст на маленькие смысловые "
    "единицы — конкретные факты, сущности (люди/организации/места), "
    "события, решения — упомянутые буквально в тексте. Не добавляй того, "
    "чего нет в тексте, не оценивай и не советуй.\n\n"
    "Ответ — МАССИВ объектов, по одному на каждую найденную единицу. "
    "Без пояснений до или после, без markdown-разметки вокруг.\n\n"
    "Допустимые значения type: ENTITY, PERSON, ORGANIZATION, PLACE, EVENT, "
    "CONCEPT, FACT, DECISION.\n\n"
    "Пример для текста «12 марта был на приёме у Петрова, кардиолога, "
    "в клинике Здоровье»:\n"
    '[{"slug": "Петров", "type": "PERSON", '
    '"text": "Кардиолог, вёл приём 12 марта.", '
    '"links": ["кардиолог", "клиника Здоровье"]},\n'
    ' {"slug": "приём кардиолога", "type": "EVENT", '
    '"text": "Приём 12 марта у кардиолога Петрова.", '
    '"links": ["Петров"]},\n'
    ' {"slug": "клиника Здоровье", "type": "ORGANIZATION", '
    '"text": "Клиника, где вёлся приём.", "links": []}]\n\n'
    "В links пиши имена других найденных единиц ровно так, как они стоят "
    "в поле slug — ничего больше. Если зацепиться не за что — верни []."
)

# Вариант B — то же, но форма ответа задана JSON-схемой (structured
# outputs Ollama), а не просьбой в тексте: модель физически не может
# вернуть одиночный объект.
SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "type": {"type": "string",
                     "enum": ["ENTITY", "PERSON", "ORGANIZATION", "PLACE",
                              "EVENT", "CONCEPT", "FACT", "DECISION"]},
            "text": {"type": "string"},
            "links": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["slug", "type", "text"],
    },
}


def ask(system, prompt, fmt):
    body = {"model": MODEL, "system": system, "prompt": prompt,
            "stream": False, "keep_alive": "0", "format": fmt}
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return (json.loads(resp.read().decode()).get("response") or "").strip()
    except Exception as exc:
        return f"<ОШИБКА: {exc}>"


engine = create_engine(get_settings().database_url)
with Session(engine) as s:
    bind_knowledge_user(s, None)
    source = s.scalars(
        select(KnowledgeSource).where(KnowledgeSource.domain == "health")
        .order_by(KnowledgeSource.created_at).limit(1)).one()
    rows = s.scalars(
        select(KnowledgeChunk.text).where(KnowledgeChunk.source_id == source.id)
        .order_by(KnowledgeChunk.ordinal)).all()
    text = "\n\n".join(rows)[:4000]
    s.rollback()

print(f"=== исходный текст ({len(text)} символов), первые 400 ===")
print(text[:400])

print("\n\n=== ВАРИАНТ A: format='json', форма просится в тексте ===")
print(ask(PROMPT_A, f"Домен: health\n\nТекст:\n{text}", "json"))

print("\n\n=== ВАРИАНТ B: format=JSON-схема массива ===")
print(ask(PROMPT_A, f"Домен: health\n\nТекст:\n{text}", SCHEMA))
PY
