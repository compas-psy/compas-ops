#!/usr/bin/env bash
# HELM · почему один и тот же вопрос отвечается через раз.
#
# ЗАЧЕМ. Прогон 541: «какой у меня был гемоглобин» дал LOCAL_NOT_FOUND
# (режим N0), а через полминуты тот же вопрос — LOCAL_ANSWER «Гемоглобин
# 167 г/л». Поиск в обоих случаях нашёл одни и те же документы: в отказе
# «Смотрел:» перечислил ровно те, что в удачном заходе стояли в
# «Источники:». Значит расходится ступень ПОСЛЕ поиска — синтез.
#
# ГИПОТЕЗА, КОТОРУЮ ЭТО ПРОВЕРЯЕТ. `synthesize_or_none` шлёт в Ollama
# тело БЕЗ `options` (synthesis.py:742) — ни `temperature`, ни `seed`.
# Значит модель генерирует с настройками по умолчанию, то есть с
# сэмплированием: каждый вызов даёт другую формулировку. Страж
# заземления (`ungrounded_numbers` и три его соседа) одни формулировки
# пропускает, другие отклоняет, и владелец видит то ответ, то «не смог
# подтвердить». Для сравнения: путь извлечения (`semantic_extract.py`)
# сделан детерминированным ещё в R4 — там стоят `temperature: 0` и
# `seed`.
#
# КАК ПРОВЕРЯЕТСЯ. Один и тот же промпт отправляется пять раз как
# сейчас и три раза с `temperature: 0` и фиксированным `seed`. Ответ
# каждый раз разбирается ТЕМ ЖЕ `parse_response`, а причина отказа
# берётся из его собственных предупреждений, а не пересказывается.
#
# ЧТО ЭТО НЕ ДЕЛАЕТ. Ничего не чинит и ничего не пишет: сессия
# откатывается, продукт не трогается. Текст ответов не печатается —
# печатаются исход, длина и причина отказа: для решения этого хватает.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
import hashlib
import json
import logging
import time
import urllib.request

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.probe import _dated_fragment, probe
from helm_core.knowledge.synthesis import (KEEP_ALIVE, MODEL_NAME, OLLAMA_URL,
                                           SYSTEM_PROMPT, build_prompt,
                                           parse_response, relevant_window)
from helm_core.knowledge.tenancy import bind_knowledge_user

QUESTION = "какой у меня был гемоглобин"
SEED = 20260910

# Причина отказа берётся из собственных предупреждений parse_response —
# пересказывать её своими словами значило бы проверять свой пересказ.
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

result = probe(session, query=QUESTION)
evidence = result.evidence
session.rollback()

print(f"  вопрос: {QUESTION}")
print(f"  [внутренний слой] исход одиночного probe: {result.outcome}, "
      f"фрагментов на входе синтеза: {len(evidence)}")
if not evidence:
    print("  фрагментов нет — синтезу нечего разбирать, замер не о том")
    raise SystemExit(0)

fragments = [_dated_fragment(item) for item in evidence]
sources = [relevant_window(QUESTION, item.chunk_text) for item in evidence]
prompt = build_prompt(QUESTION, fragments)
print(f"  модель: {MODEL_NAME}; длина промпта: {len(prompt)} символов")
print()


def attempt(options: dict | None) -> str:
    """Один вызов ровно тем же телом, что шлёт продукт, плюс options."""
    body = {"model": MODEL_NAME, "prompt": prompt, "system": SYSTEM_PROMPT,
            "stream": False, "keep_alive": KEEP_ALIVE}
    if options:
        body["options"] = options
    request = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    reasons.clear()
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = (json.loads(response.read().decode()).get("response") or "")
    spent = time.monotonic() - started
    parsed = parse_response(raw, fragments=fragments, sources=sources)

    if parsed is None:
        verdict = "формат не выдержан"
    elif parsed.answered:
        verdict = f"ОТВЕТ, фрагментов {len(parsed.used)}"
    elif parsed.verified:
        verdict = "модель говорит: ответа в источниках нет"
    else:
        verdict = "ОТКЛОНЁН стражем"
    digest = hashlib.sha1(raw.strip().encode()).hexdigest()[:8]
    print(f"    {verdict}; сырой ответ: {len(raw.strip())} симв, отпечаток {digest}"
          f"  [{spent:.0f} с]")
    for reason in reasons:
        print(f"      причина: {reason}")
    return verdict.split(",")[0]


print("############ КАК СЕЙЧАС: БЕЗ OPTIONS ############")
as_is = [attempt(None) for _ in range(5)]

print()
print("############ С temperature: 0 И ФИКСИРОВАННЫМ SEED ############")
seeded = [attempt({"temperature": 0, "seed": SEED}) for _ in range(3)]

print()
print("############ ИТОГ ############")
print(f"  без options: {len(set(as_is))} разных исхода на 5 вызовов — {as_is}")
print(f"  с temperature 0 и seed: {len(set(seeded))} на 3 вызова — {seeded}")
print("  (прямые вызовы Ollama, мимо ворот model_gate: замер, не путь бота)")
PYEOF
