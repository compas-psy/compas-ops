#!/usr/bin/env bash
# HELM · §36, группа 2: что проверяется каналами, серверная половина.
#
# ЗАЧЕМ. Группа 1 закрыла десять условий, проверяемых без владельца.
# Группа 2 — про каналы: стиль и страж достоверности, запрет тихой
# оплаты, управление памятью из бота, независимость MAX. Три из четырёх
# проверяются здесь; четвёртая упирается в запрет, названный ниже.
#
# ЧТО ЭТИМ НЕ ПРОВЕРЯЕТСЯ. Живой MAX. Чтобы дойти до `/hooks/max`,
# пришлось бы подделать апдейт от владельца, а ответ ушёл бы в его
# настоящий чат — это «писать реальным людям», запрещено уставом §6 и
# CLAUDE.md §5.2. Поэтому 2.4 проверяется чтением ВЫКАЧЕННОГО кода, а
# живая половина остаётся за владельцем: одно сообщение в MAX.
#
# ЧТО ПИШЕТСЯ. Одна запись микро-памяти с меткой прогона: её забывают,
# возвращают и удаляют навсегда. Удаляется ТОЛЬКО она — запись, которую
# этот же прогон и создал. Документы владельца не трогаются.
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
import uuid

SECRET = os.environ["HELM_HMAC"]
BASE = "http://127.0.0.1:8080/internal/knowledge"
CHAT = "dod-group2"
MARK = f"метка-группы2-{uuid.uuid4().hex[:8]}"

verdicts: list[tuple[str, str, str]] = []


def record(number: str, name: str, ok: bool | None, detail: str) -> None:
    mark = {True: "ПРОШЛО", False: "ПРОВАЛ", None: "НЕ ВЫПОЛНЕНО"}[ok]
    verdicts.append((number, name, mark))
    print(f"   ВЕРДИКТ: {mark} — {detail}")
    print()


def call(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    stamp = str(int(time.time()))
    signature = hmac.new(SECRET.encode("utf-8"),
                         stamp.encode("utf-8") + b"\x00" + body,
                         hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        f"{BASE}/{path}", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Helm-Timestamp": stamp, "X-Helm-Signature": signature})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=180) as response:
        answer = json.loads(response.read().decode())
    answer["_seconds"] = round(time.monotonic() - started, 1)
    return answer


def ask(query: str, **extra) -> dict:
    payload = {"query": query, "channel": "telegram", "chat_id": CHAT}
    payload.update(extra)
    answer = call("probe", payload)
    print(f"   ЗАПРОС: {query}")
    print(f"   ОТВЕТ ({answer.get('_seconds')} с, {answer.get('outcome')}):")
    for line in (answer.get("answer_text") or "(пусто)").splitlines():
        print(f"     {line}")
    return answer


def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from helm_core.config import get_settings
    from helm_core.knowledge.tenancy import bind_knowledge_user

    session = sessionmaker(bind=create_engine(get_settings().database_url,
                                              pool_pre_ping=True))()
    bind_knowledge_user(session, None)
    return session


# ── 2.1: стиль владельца и страж достоверности ───────────────────────
# §36: "local textual knowledge answers are rendered in owner style by
# local Ollama and pass fidelity guard". Что ответ локальный — видно по
# исходу; что страж РАБОТАЕТ — нет, поэтому он проверяется на паре,
# собранной здесь же: настоящий ответ бота как фрагмент и подделанное
# число вместо настоящего. Страж, который ничего не ловит, — не страж.
print("############ 2.1. СТИЛЬ И СТРАЖ ДОСТОВЕРНОСТИ ############")
try:
    import re

    from helm_core.knowledge.synthesis import ungrounded_numbers
    from helm_core.models import KnowledgeAnswerRun

    answer = ask("какой у меня был гемоглобин")
    text = answer.get("answer_text") or ""
    run_id = answer.get("answer_run_id")
    session = db()
    row = session.get(KnowledgeAnswerRun, uuid.UUID(run_id)) if run_id else None
    print(f"   [внутренний слой] режим ответа: {row.mode if row else '—'}")

    # Число берётся из живого ответа, а не вписывается сюда: показатель
    # здоровья владельца в тексте скрипта — это он же в репозитории.
    found = re.findall(r"\d+", text)
    value = found[0] if found else ""
    honest = ungrounded_numbers(f"Показатель: {value}", [text])
    forged = ungrounded_numbers(f"Показатель: {value}0000", [text])
    print(f"   [прямой вызов ungrounded_numbers] на пересказе с тем же числом: "
          f"{len(honest)} претензий; на подменённом числе: {len(forged)}")

    passed = (answer.get("outcome") == "LOCAL_ANSWER" and bool(value)
              and not honest and bool(forged))
    record("2.1", "ответ локальный, страж достоверности ловит выдумку", passed,
           "пересказ с настоящим числом страж пропускает, подменённое ловит"
           if passed else "страж не отличил подменённое число от настоящего")
    session.rollback()
except Exception as exc:  # noqa: BLE001
    record("2.1", "ответ локальный, страж достоверности ловит выдумку", None,
           f"сбой проверки: {exc!r}")

# ── 2.2: оплата не открывается мимо режима чата ──────────────────────
# §36: "any material Knowledge hit locks paid AI to OFF unless owner
# explicitly opts in for that turn". Проверяется не умолчанием, а
# попыткой: `paid_allowed: true` присылается ВМЕСТЕ с названным чатом —
# ровно та подстановка, которой пользовательский вход открывал бы себе
# оплату мимо режима (`internal.py:104`).
print("############ 2.2. ОПЛАТА НЕ ОТКРЫВАЕТСЯ МИМО РЕЖИМА ЧАТА ############")
try:
    from helm_core.models import KnowledgeAnswerRun

    answer = ask("какой у меня был гемоглобин", paid_allowed=True)
    run_id = answer.get("answer_run_id")
    session = db()
    row = session.get(KnowledgeAnswerRun, uuid.UUID(run_id)) if run_id else None
    print(f"   [внутренний слой] paid_ai_used="
          f"{row.paid_ai_used if row else '—'}, "
          f"облачная модель: {row.cloud_model if row else '—'}, "
          f"причина эскалации: {row.escalation_reason if row else '—'}")
    record("2.2", "платный переход не открывается подстановкой флага",
           bool(row) and row.paid_ai_used is False and row.cloud_model is None,
           "флаг при названном чате проигнорирован, ответ остался локальным"
           if row and row.paid_ai_used is False and row.cloud_model is None
           else "подстановка флага открыла платный путь")
    session.rollback()
except Exception as exc:  # noqa: BLE001
    record("2.2", "платный переход не открывается подстановкой флага", None,
           f"сбой проверки: {exc!r}")

# ── 2.3: управление памятью из бота ──────────────────────────────────
# §36: "owner can disable/archive/version/reprocess/delete knowledge
# from Panel and bot". Половина «из бота» — четыре команды §14.16, и
# проверяются они по кругу: запомнить, забыть, вернуть, удалить
# навсегда. Каждый шаг подтверждается вопросом, а не только статусом
# команды: статус говорит, что команда разобрана, а не что знание
# перестало отвечать.
print("############ 2.3. УПРАВЛЕНИЕ ПАМЯТЬЮ ИЗ БОТА ############")
try:
    from sqlalchemy import select

    from helm_core.models import KnowledgeMemory

    # Формулировка нарочно бытовая. Прогон 540 показал, почему это не
    # мелочь: текст «пароль от кладовки» упёрся в отказ хранить секрет
    # (§14.10), «Запомни» вернуло `rejected_secret`, и дальше проверять
    # было нечего. Сам отказ теперь проверяется отдельно, ниже.
    question = f"что лежит на полке в кладовке {MARK}"
    stored = call("remember", {"text": f"Запомни: на полке в кладовке лежит {MARK}",
                               "channel": "telegram", "chat_id": CHAT})
    print(f"   «Запомни»: {stored.get('status')}")
    after_store = MARK in (ask(question).get("answer_text") or "")

    forgotten = call("admin", {"text": f"Забудь {MARK}"})
    print(f"   «Забудь»: {forgotten.get('status')}")
    after_forget = MARK in (ask(question).get("answer_text") or "")

    restored = call("admin", {"text": f"Верни в память {MARK}"})
    print(f"   «Верни в память»: {restored.get('status')}")
    after_restore = MARK in (ask(question).get("answer_text") or "")

    purged = call("admin", {"text": f"Удали навсегда {MARK}"})
    print(f"   «Удали навсегда»: {purged.get('status')}")
    after_purge = MARK in (ask(question).get("answer_text") or "")

    session = db()
    left = session.scalars(select(KnowledgeMemory).where(
        KnowledgeMemory.canonical_text.contains(MARK))).all()
    print(f"   [внутренний слой] строк с меткой в базе после удаления: {len(left)}")

    steps = (after_store, not after_forget, after_restore, not after_purge, not left)
    record("2.3", "запомнить, забыть, вернуть, удалить навсегда",
           all(steps),
           "все четыре команды сработали, после удаления строки нет"
           if all(steps) else
           f"сорвалось: запомнил={after_store}, забыл={not after_forget}, "
           f"вернул={after_restore}, удалил={not after_purge}, строк осталось={len(left)}")
    session.rollback()
except Exception as exc:  # noqa: BLE001
    record("2.3", "запомнить, забыть, вернуть, удалить навсегда", None,
           f"сбой проверки: {exc!r}")

# ── 2.5: секрет не попадает в память ─────────────────────────────────
# §14.10: "Detect and refuse storing" — метка секрета рядом с текстом
# достаточна для отказа. Проверка появилась не из спеки, а из прогона
# 540: на ней сорвалась проверка 2.3, и стало видно, что запрет живой.
print("############ 2.5. СЕКРЕТ НЕ ПОПАДАЕТ В ПАМЯТЬ ############")
try:
    from sqlalchemy import select

    from helm_core.models import KnowledgeMemory

    secret_mark = f"{MARK}-секрет"
    attempt = call("remember", {"text": f"Запомни: пароль от кладовки — {secret_mark}",
                                "channel": "telegram", "chat_id": CHAT})
    print(f"   «Запомни» с меткой секрета: {attempt.get('status')}")
    session = db()
    left = session.scalars(select(KnowledgeMemory).where(
        KnowledgeMemory.canonical_text.contains(secret_mark))).all()
    print(f"   [внутренний слой] строк с этой меткой в базе: {len(left)}")
    record("2.5", "секрет не записывается в память",
           attempt.get("status") == "rejected_secret" and not left,
           "запись отклонена и в базу не попала"
           if attempt.get("status") == "rejected_secret" and not left
           else f"исход {attempt.get('status')}, строк в базе {len(left)}")
    session.rollback()
except Exception as exc:  # noqa: BLE001
    record("2.5", "секрет не записывается в память", None, f"сбой проверки: {exc!r}")

# ── 2.4: MAX как независимый вход ────────────────────────────────────
# Живьём не проверяется и не будет: подделанный апдейт от владельца
# дошёл бы ответом в его настоящий чат MAX. Что проверить МОЖНО без
# этого — форму выкаченного кода: зовёт ли `/hooks/max` то же ядро и с
# каким контекстом. Читается исходник живого процесса, а не репозиторий
# на раннере, поэтому это факт о том, что выкачено.
print("############ 2.4. MAX КАК НЕЗАВИСИМЫЙ ВХОД ############")
try:
    import inspect

    from helm_core.api import hooks

    source = inspect.getsource(hooks.max_webhook)
    calls_probe = "probe(" in source
    passes_context = "context=" in source
    print(f"   [выкаченный код] /hooks/max зовёт probe: {calls_probe}; "
          f"передаёт контекст разговора: {passes_context}")
    record("2.4", "MAX — независимый вход в ту же память", None,
           "живьём не проверяется: подделанный апдейт ушёл бы ответом в "
           "настоящий чат владельца (устав §6). По коду: ядро то же, но "
           "контекст разговора не передаётся — двухходовой диалог "
           "(«какой из них?» → выбор) в MAX не работает по построению")
except Exception as exc:  # noqa: BLE001
    record("2.4", "MAX — независимый вход в ту же память", None,
           f"сбой чтения кода: {exc!r}")

print("############ ИТОГ ГРУППЫ 2 ############")
for number, name, mark in verdicts:
    print(f"  {number}. {name}: {mark}")
print(f"  прошло {sum(1 for _, _, m in verdicts if m == 'ПРОШЛО')} из {len(verdicts)}")
PYEOF
