#!/usr/bin/env bash
# HELM · выдача оригинала: доводится ли уточнение до файла.
#
# ЗАЧЕМ. Скриншоты владельца 10.09.2026: на «отдай файл последнего
# клинического анализа крови» бот перечисляет документы и спрашивает
# «Какой из них?», а на ответ присылает пересказ. Тупик: список
# кандидатов нигде не жил, следующая реплика уходила обычным путём.
#
# ПОЧЕМУ ИМЕННО ЭТОТ ВХОД. Диалог владельца идёт в Telegram, а там
# состояние разговора держит плагин `helm-control` и присылает его в
# `context` следующего запроса (`__init__.py:811`). Серверная сторона
# этого пути — `/internal/knowledge/probe` с HMAC-подписью, и здесь
# зовётся именно она, двумя ходами, с контекстом ровно той формы, что
# шлёт плагин.
#
# ЧТО ЭТИМ НЕ ПРОВЕРЯЕТСЯ. Сам процесс плагина (пять строк, которые
# кладут sources прошлого ответа в context) и путь `/hooks/max`: у MAX
# состояния разговора нет вовсе — probe там зовётся БЕЗ context
# (`hooks.py:387`), значит двухходовой диалог в MAX не работает по
# построению. Сказано вслух, а не умолчано.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

SECRET=$(sudo cat /etc/helm/secrets/hermes_service_hmac 2>/dev/null || echo "")
if [ -z "$SECRET" ]; then
  echo "секрета подписи нет — приёмка не выполняется"
  exit 1
fi

sudo docker compose exec -T -e HELM_HMAC="$SECRET" helm-core python3 - <<'PYEOF'
import hashlib
import hmac
import json
import os
import time
import urllib.request

SECRET = os.environ["HELM_HMAC"]
URL = "http://127.0.0.1:8080/internal/knowledge/probe"


def ask(query: str, context: dict | None = None) -> dict:
    payload = {"query": query, "channel": "telegram", "chat_id": "acceptance"}
    if context is not None:
        payload["context"] = context
    body = json.dumps(payload).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(SECRET.encode("utf-8"),
                         timestamp.encode("utf-8") + b"\x00" + body,
                         hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Helm-Timestamp": timestamp,
                 "X-Helm-Signature": signature})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=180) as response:
        answer = json.loads(response.read().decode())
    answer["_seconds"] = round(time.monotonic() - started, 1)
    return answer


def show(label: str, query: str, answer: dict) -> None:
    print(f"── {label}")
    print(f"   ЗАПРОС: {query}")
    print(f"   ОТВЕТ ({answer.get('_seconds')} с, исход {answer.get('outcome')}):")
    for line in (answer.get("answer_text") or "(пусто)").splitlines():
        print(f"     {line}")
    named = [s.get("original_filename") for s in answer.get("sources") or []]
    print(f"   источников в ответе: {len(named)}"
          + (f" — {'; '.join(str(n) for n in named)}" if named else ""))
    print()


def context_from(question: str, answer: dict) -> dict:
    """Ровно то, что кладёт плагин: вопрос и источники прошлого ответа."""
    sources = answer.get("sources") or []
    return {
        "question": question,
        "source_ids": [s["source_id"] for s in sources if s.get("source_id")][:10],
        "filenames": [s["original_filename"] for s in sources
                      if s.get("original_filename")][:10],
        "memory": True,
    }


print()
print("############ ХОД 1: ПРОСЬБА ОТДАТЬ ФАЙЛ ############")
first_query = "Отдай мне файл последнего клинического анализа крови"
first = ask(first_query)
show("ход 1", first_query, first)

if "Какой из них?" not in (first.get("answer_text") or ""):
    print("  уточнения не было — проверять выбор не на чем")
    raise SystemExit(0)
if not (first.get("sources") or []):
    print("  ПРОВАЛ: бот спросил «какой из них», но не назвал документы наружу —")
    print("  значит плагину нечего положить в контекст, и выбор снова умрёт.")
    raise SystemExit(1)

context = context_from(first_query, first)

print("############ ХОД 2: ВЛАДЕЛЕЦ НАЗЫВАЕТ ДОКУМЕНТ ############")
for label, query in (
        ("как в переписке 10.09", "Вот этот: 148990953_Исследования гликированного гемоглобина.pdf"),
        ("словами, не именем файла", "Исследование гликированного гемоглобина - последний"),
        ("просто имя файла", "148990953_Исследования гликированного гемоглобина.pdf")):
    answer = ask(query, context)
    show(label, query, answer)
    verdict = ("ВЫДАЛ ССЫЛКУ НА ОРИГИНАЛ"
               if "ключом доступа" in (answer.get("answer_text") or "")
               else "НЕ ВЫДАЛ — снова ответил не тем")
    print(f"   ИТОГ: {verdict}")
    print()

print("############ ХОД 3: ПОСТОРОННИЙ ВОПРОС ПОСЛЕ УТОЧНЕНИЯ ############")
print("(выбор не должен захватывать разговор: это обычный вопрос)")
other_query = "а какой у меня был гемоглобин"
other = ask(other_query, context)
show("посторонний вопрос", other_query, other)
print("   ИТОГ: " + ("ОШИБКА — принял за выбор документа"
                     if "ключом доступа" in (other.get("answer_text") or "")
                     else "верно: остался обычным вопросом"))
PYEOF
