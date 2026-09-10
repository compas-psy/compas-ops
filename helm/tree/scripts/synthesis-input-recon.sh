#!/usr/bin/env bash
# HELM · тот же вход синтеза, восемь вызовов: где рождается разброс.
#
# ЗАЧЕМ. Замер 543 подавал синтезу `ProbeResult.evidence` и получил
# пять ответов из пяти. Это была ошибка чтения: при успешном ответе в
# этом поле лежит НЕ вход синтеза, а его выход — только процитированные
# фрагменты (`probe.py:1278`). Настоящий вход строится строкой 1024,
# `candidates[:MAX_EVIDENCE]`, и это до пяти бланков разных лет, где
# модели надо выбрать. На одном процитированном фрагменте задача
# тривиальна, и замер её и померил.
#
# КАК ЗДЕСЬ ПРАВИЛЬНО. Аргументы не воспроизводятся по памяти, а
# перехватываются: `synthesize_or_none` в модуле probe на время ОДНОГО
# живого вызова подменяется обёрткой, которая запоминает переданное и
# зовёт оригинал. Дальше повторные вызовы идут ровно на том, что ушло
# модели в продакшне.
#
# ЧТО ПРОВЕРЯЕТСЯ. Пять вызовов как сейчас (без `options`) и три с
# `temperature: 0` и фиксированным seed. Разбор — тем же
# `parse_response`, причина отказа — его собственным текстом.
#
# ЧТО ЭТО НЕ ДЕЛАЕТ. Не чинит и не пишет: подмена живёт внутри этого
# процесса и снимается сразу, сессия откатывается. Тексты ответов не
# печатаются — печатаются исход, длина, отпечаток и причина отказа.
set -uo pipefail
cd /opt/helm/compose || exit 1

QUESTION="${1:-какой у меня был гемоглобин}"

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - "$QUESTION" <<'PYEOF'
import collections
import hashlib
import json
import logging
import sys
import time
import urllib.request

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge import probe as probe_module
from helm_core.knowledge.synthesis import (KEEP_ALIVE, MODEL_NAME, OLLAMA_URL,
                                           SYSTEM_PROMPT, build_prompt,
                                           parse_response, relevant_window)
from helm_core.knowledge.tenancy import bind_knowledge_user

QUESTION = sys.argv[1]
SEED = 20260910

reasons: list[str] = []


class Collect(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        reasons.append(record.getMessage())


log = logging.getLogger("helm_core.knowledge.synthesis")
log.addHandler(Collect())
log.setLevel(logging.WARNING)

session = sessionmaker(bind=create_engine(get_settings().database_url,
                                          pool_pre_ping=True))()
bind_knowledge_user(session, None)

# Перехват аргументов ЖИВОГО вызова: что именно ушло модели.
captured: dict = {}
original = probe_module.synthesize_or_none


def spy(question, fragments, *, sources=None):
    captured["question"] = question
    captured["fragments"] = list(fragments)
    captured["sources"] = list(sources) if sources is not None else None
    return original(question, fragments, sources=sources)


probe_module.synthesize_or_none = spy
try:
    result = probe_module.probe(session, query=QUESTION)
finally:
    probe_module.synthesize_or_none = original
session.rollback()

print(f"  вопрос: {QUESTION}")
print(f"  [внутренний слой] исход живого вызова: {result.outcome}, режим {result.mode}")
if "fragments" not in captured:
    print("  синтез не звался — ответ пришёл другим путём, мерить нечего")
    raise SystemExit(0)

fragments = captured["fragments"]
sources = [relevant_window(captured["question"], text)
           for text in (captured["sources"] or fragments)]
prompt = build_prompt(captured["question"], fragments)
print(f"  ФРАГМЕНТОВ УШЛО МОДЕЛИ: {len(fragments)}; длина промпта: {len(prompt)} символов")
print(f"  модель: {MODEL_NAME}")
print()


def attempt(options: dict | None) -> str:
    body = {"model": MODEL_NAME, "prompt": prompt, "system": SYSTEM_PROMPT,
            "stream": False, "keep_alive": KEEP_ALIVE}
    if options:
        body["options"] = options
    request = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    reasons.clear()
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = (json.loads(response.read().decode()).get("response") or "")
    spent = time.monotonic() - started
    parsed = parse_response(raw, fragments=fragments, sources=sources)

    if parsed is None:
        verdict = "формат не выдержан"
    elif parsed.answered:
        verdict = f"ОТВЕТ (сослался на {len(parsed.used)})"
    elif parsed.verified:
        verdict = "модель: ответа в источниках нет"
    else:
        verdict = "ОТКЛОНЁН стражем"
    digest = hashlib.sha1(raw.strip().encode()).hexdigest()[:8]
    print(f"    {verdict}; сырой ответ {len(raw.strip())} симв, отпечаток {digest}"
          f"  [{spent:.0f} с]")
    for reason in reasons:
        print(f"      причина: {reason}")
    return verdict.split(" (")[0]


print("############ КАК СЕЙЧАС: БЕЗ OPTIONS ############")
as_is = [attempt(None) for _ in range(5)]

print()
print("############ С temperature: 0 И ФИКСИРОВАННЫМ SEED ############")
seeded = [attempt({"temperature": 0, "seed": SEED}) for _ in range(3)]

print()
print("############ ИТОГ ############")
print(f"  без options: {dict(collections.Counter(as_is))}")
print(f"  с temperature 0 и seed: {dict(collections.Counter(seeded))}")
print("  (прямые вызовы Ollama, мимо ворот model_gate: замер, не путь бота)")
PYEOF
