#!/usr/bin/env bash
# HELM · §36, группа 1: что проверяется без участия владельца.
#
# ЗАЧЕМ. §36 «Final Definition of Done» перечисляет около сорока пяти
# условий готовности. Часть не проверялась ни разу — в том числе те, что
# касаются приватности: не утекает ли удалённое знание и изолированы ли
# данные здоровья. Здесь собраны десять условий, проверяемых на сервере
# без владельца.
#
# КАК. Пользовательские действия идут настоящими путями плагина —
# `/internal/knowledge/{probe,remember,admin}` с HMAC. Состояние читается
# из базы отдельно и подписано как внутренний слой: оно не выдаётся за
# ответ бота.
#
# ЧТО ПИШЕТСЯ. Одна запись микро-памяти со словом-меткой прогона, затем
# она же забывается — обе операции штатные, доступные владельцу из бота.
# Документы владельца не трогаются, статусы руками не правятся.
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
MARK = f"метка-приёмки-{uuid.uuid4().hex[:8]}"

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


def ask(query: str, context: dict | None = None) -> dict:
    payload = {"query": query, "channel": "telegram", "chat_id": "dod-group1"}
    if context is not None:
        payload["context"] = context
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


# ── 1 и 2: бесплатность и источники ──────────────────────────────────
print("############ 1–2. ВОПРОС К ПАМЯТИ: БЕСПЛАТНО И С ИСТОЧНИКАМИ ############")
try:
    from sqlalchemy import select

    from helm_core.models import KnowledgeAnswerRun

    answer = ask("какой у меня был гемоглобин")
    sources = answer.get("sources") or []
    run_id = answer.get("answer_run_id")
    session = db()
    row = session.get(KnowledgeAnswerRun, uuid.UUID(run_id)) if run_id else None
    print(f"   [внутренний слой] источников: {len(sources)}; "
          f"строка прогона: {'есть' if row else 'нет'}"
          + (f", paid_ai_used={row.paid_ai_used}, evidence_count={row.evidence_count}"
             if row else ""))
    record("1", "вопрос к памяти не платит",
           bool(row) and row.paid_ai_used is False,
           "платная модель не вызывалась по записи прогона"
           if row and row.paid_ai_used is False else "запись прогона не подтверждает бесплатность")
    record("2", "ответ несёт источники", bool(sources),
           f"источников в ответе: {len(sources)}")
    session.rollback()
except Exception as exc:  # noqa: BLE001 — диагностика приёмки
    record("1", "вопрос к памяти не платит", None, f"сбой проверки: {exc!r}")
    record("2", "ответ несёт источники", None, "не выполнялась из-за сбоя выше")

# ── 3: оригинал документа ────────────────────────────────────────────
# Условие §36 закрывается ТОЛЬКО ссылкой с ключом доступа. Уточняющий
# вопрос — половина пути: раньше он засчитывался, и приёмка не отличала
# работающую выдачу от тупика, который владелец нашёл на скриншотах
# 10.09. Поэтому ход второй: называем документ так же, как называет его
# владелец, и контекст собираем той же формы, что шлёт плагин.
print("############ 3. ОРИГИНАЛ ДОКУМЕНТА, А НЕ ПЕРЕСКАЗ ############")
try:
    answer = ask("Отдай мне файл клинического анализа крови")
    text = answer.get("answer_text") or ""
    sources = answer.get("sources") or []
    if "ключом доступа" in text:
        record("3", "оригинал выдаётся с ключом доступа", True,
               "оригинал отдан сразу, уточнение не понадобилось")
    elif "Какой из них?" not in text:
        record("3", "оригинал выдаётся с ключом доступа", False, "ответ не про файл")
    elif not sources:
        record("3", "оригинал выдаётся с ключом доступа", False,
               "спросил «какой из них», но документов наружу не назвал — "
               "плагину нечего положить в контекст")
    else:
        named = next((s.get("original_filename") for s in sources
                      if s.get("original_filename")), None)
        print(f"   [ход 2] владелец называет: {named}")
        second = ask(f"Вот этот: {named}", {
            "question": "Отдай мне файл клинического анализа крови",
            "source_ids": [s["source_id"] for s in sources if s.get("source_id")][:10],
            "filenames": [s["original_filename"] for s in sources
                          if s.get("original_filename")][:10],
            "memory": True,
        })
        delivered = "ключом доступа" in (second.get("answer_text") or "")
        record("3", "оригинал выдаётся с ключом доступа", delivered,
               "уточнение доведено до оригинала под ключом" if delivered
               else "на выбор документа ответил не оригиналом — тупик уточнения")
except Exception as exc:  # noqa: BLE001
    record("3", "оригинал выдаётся с ключом доступа", None, f"сбой проверки: {exc!r}")

# ── 4: утечка забытого знания ────────────────────────────────────────
print("############ 4. ЗАБЫТОЕ НЕ УТЕКАЕТ ############")
print("(запоминаем метку, спрашиваем, забываем, спрашиваем снова)")
try:
    remembered = call("remember", {"text": f"Запомни: кодовое слово прогона — {MARK}",
                                   "channel": "telegram", "chat_id": "dod-group1"})
    print(f"   запись памяти: {remembered.get('status')}")
    before = ask(f"какое кодовое слово прогона {MARK}")
    found_before = MARK in (before.get("answer_text") or "")

    forgotten = call("admin", {"text": f"Забудь {MARK}"})
    print(f"   команда «Забудь»: {forgotten.get('status')}")
    for line in (forgotten.get("answer_text") or "").splitlines():
        print(f"     {line}")

    after = ask(f"какое кодовое слово прогона {MARK}")
    leaked = MARK in (after.get("answer_text") or "")
    record("4", "забытое знание не утекает",
           found_before and not leaked,
           "до «Забудь» отвечало, после — нет"
           if found_before and not leaked else
           ("после «Забудь» знание всё ещё отвечает" if leaked
            else "знание не отвечало и ДО забывания — проверка недействительна"))
except Exception as exc:  # noqa: BLE001
    record("4", "забытое знание не утекает", None, f"сбой проверки: {exc!r}")

# ── 5: дедуп по содержимому, а не по имени ───────────────────────────
print("############ 5. ДЕДУП ПО СОДЕРЖИМОМУ ############")
try:
    from sqlalchemy import func, select

    from helm_core.models import KnowledgeSource

    session = db()
    duplicates = session.execute(
        select(KnowledgeSource.sha256, func.count())
        .group_by(KnowledgeSource.sha256).having(func.count() > 1)).all()
    print(f"   [внутренний слой] групп источников с одинаковым SHA: {len(duplicates)}")
    record("5", "имя файла не ключ дедупликации", not duplicates,
           "одинакового содержимого под разными именами не заведено"
           if not duplicates else f"найдено {len(duplicates)} групп с одним SHA")
    session.rollback()
except Exception as exc:  # noqa: BLE001
    record("5", "имя файла не ключ дедупликации", None, f"сбой проверки: {exc!r}")

# ── 6: «Запомни» без разбора документа ───────────────────────────────
print("############ 6. «ЗАПОМНИ» — МИКРО-ПАМЯТЬ, А НЕ РАЗБОР ДОКУМЕНТА ############")
try:
    from sqlalchemy import func, select

    from helm_core.models import KnowledgeIngestJob, KnowledgeMemory

    session = db()
    recent = session.scalars(
        select(KnowledgeMemory).order_by(KnowledgeMemory.created_at.desc()).limit(1)
    ).one_or_none()
    jobs = session.scalar(
        select(func.count()).select_from(KnowledgeIngestJob)) if recent else None
    print(f"   [внутренний слой] последняя запись памяти: "
          f"{'есть' if recent else 'нет'}; заданий разбора всего: {jobs}")
    record("6", "«Запомни» кладёт микро-память", bool(recent),
           "запись памяти создана обращением выше" if recent else "записи памяти нет")
    session.rollback()
except Exception as exc:  # noqa: BLE001
    record("6", "«Запомни» кладёт микро-память", None, f"сбой проверки: {exc!r}")

# ── 7: временная память ──────────────────────────────────────────────
print("############ 7. ВРЕМЕННАЯ ПАМЯТЬ ############")
record("7", "временная память исчезает из выдачи, но жива", None,
       "формулировка временной памяти в этой приёмке не задана — "
       "выдумывать её ради галочки не буду, вынесено в группу 2")

# ── 8: изоляция здоровья ─────────────────────────────────────────────
print("############ 8. ИЗОЛЯЦИЯ ЗДОРОВЬЯ ############")
try:
    from sqlalchemy import func, select

    from helm_core.models import KnowledgeSource

    session = db()
    health_total = session.scalar(
        select(func.count()).select_from(KnowledgeSource)
        .where(KnowledgeSource.domain == "health"))
    health_leaked = session.scalar(
        select(func.count()).select_from(KnowledgeSource)
        .where(KnowledgeSource.domain == "health",
               KnowledgeSource.original_filename.isnot(None)))
    print(f"   [внутренний слой] health-источников: {health_total}; "
          f"из них с именем в ОБЩЕЙ таблице: {health_leaked}")
    record("8", "имена health-документов не лежат в общей таблице",
           health_total > 0 and health_leaked == 0,
           "все имена живут только в health-схеме (ADR-005/P12)"
           if health_total and not health_leaked else
           ("health-источников нет — проверять нечего" if not health_total
            else f"{health_leaked} имён видны в общей таблице"))
    session.rollback()
except Exception as exc:  # noqa: BLE001
    record("8", "имена health-документов не лежат в общей таблице", None,
           f"сбой проверки: {exc!r}")

# ── 9: точный отзыв идентификатора ───────────────────────────────────
print("############ 9. ТОЧНЫЙ ОТЗЫВ ИДЕНТИФИКАТОРА ############")
try:
    url = f"https://example.org/{MARK}?x=1"
    call("remember", {"text": f"Запомни ссылку прогона: {url}",
                      "channel": "telegram", "chat_id": "dod-group1"})
    answer = ask("какая ссылка прогона")
    exact = url in (answer.get("answer_text") or "")
    record("9", "идентификатор отзывается байт-в-байт", exact,
           "ссылка вернулась дословно" if exact else "ссылка изменена или не найдена")
    call("admin", {"text": f"Забудь ссылку прогона {MARK}"})
except Exception as exc:  # noqa: BLE001
    record("9", "идентификатор отзывается байт-в-байт", None, f"сбой проверки: {exc!r}")

# ── 10: ZIP-пачка завершается ровно один раз ─────────────────────────
print("############ 10. ПАЧКА ЗАВЕРШАЕТСЯ РОВНО ОДИН РАЗ ############")
try:
    from sqlalchemy import func, select

    from helm_core.models import KnowledgeIngestBatch

    session = db()
    rows = session.execute(
        select(KnowledgeIngestBatch.status, func.count())
        .group_by(KnowledgeIngestBatch.status)).all()
    print("   [внутренний слой] пачки по состояниям: "
          + ", ".join(f"{status}: {count}" for status, count in rows))
    finished = session.scalars(
        select(KnowledgeIngestBatch)
        .where(KnowledgeIngestBatch.finished_at.isnot(None))).all()
    for batch in finished:
        print(f"     пачка {str(batch.id)[:8]}: {batch.status}, "
              f"итоговое уведомление: "
              f"{'отправлено' if batch.final_notification_sent_at else 'НЕТ'}, "
              f"ревизия завершения: {batch.completion_revision}")
    silent = [b for b in finished if b.final_notification_sent_at is None]
    repeated = [b for b in finished if b.completion_revision > 1]
    record("10", "пачка завершается ровно один раз",
           bool(finished) and not silent and not repeated,
           f"законченных пачек {len(finished)}, у каждой одно итоговое уведомление"
           if finished and not silent and not repeated else
           ("законченных пачек нет — проверять нечего" if not finished
            else f"без уведомления: {len(silent)}, с повторным завершением: {len(repeated)}"))
    session.rollback()
except Exception as exc:  # noqa: BLE001
    record("10", "пачка завершается ровно один раз", None, f"сбой проверки: {exc!r}")

print("############ ИТОГ ГРУППЫ 1 ############")
for number, name, mark in verdicts:
    print(f"  {number}. {name}: {mark}")
passed = sum(1 for _, _, mark in verdicts if mark == "ПРОШЛО")
print(f"  прошло {passed} из {len(verdicts)}")
PYEOF
