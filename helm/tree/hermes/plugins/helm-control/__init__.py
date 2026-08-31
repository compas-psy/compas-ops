"""helm-control — Control Plane gate до LLM-вызова (ТЗ §9.3).

НАЙДЕНО на живом Telegram-тесте: директория ``~/.hermes/hooks/<name>/``
(``HOOK.yaml`` + ``handler.py``, куда этот плагин был помещён изначально)
— это ДРУГОЙ, чисто уведомительный механизм (``gateway/hooks.py::HookRegistry``,
события вида ``agent:start``/``session:end`` через двоеточие, docstring
модуля прямо говорит "Errors ... never block the main pipeline"). Он не
имеет отношения к ``pre_gateway_dispatch``/``pre_llm_call`` — те дёргает
исключительно ``hermes_cli/plugins.py::PluginManager`` через
``~/.hermes/plugins/<name>/`` (``plugin.yaml`` + ``register(ctx)``) и
только если имя плагина явно включено в ``plugins.enabled`` в конфиге
(opt-in по умолчанию). Хук в старой директории исправно "загружался"
(лог "[hooks] Loaded hook ...") — просто событие, под которое он был
зарегистрирован, там никто никогда не вызывает, поэтому LLM всё это
время вызывалась мимо Control Plane без единой ошибки в логах.

pre_gateway_dispatch регистрирует входящее сообщение в Control Plane ДО
того, как оно дойдёт до LLM. Если Control Plane не подтверждает
регистрацию (недоступен, отверг подпись, отверг owner_id) — сообщение до
модели не доходит: это fail-closed по конструкции, не по доп. проверке,
потому что LLM вызывается уже ПОСЛЕ этой функции, а не внутри неё.

pre_llm_call передаёт HELM_TASK_ID зарегистрированной задачи в модель
коротким контекстом (§9.3: "короткий trusted context").

Секрет HMAC читается из того же docker secret, что видит helm-core
(/run/secrets/hermes_service_hmac на хосте, где эта обвязка запущена
вне контейнера — путь совпадает, т.к. Hermes работает на хосте, а не
в Docker, и секреты лежат в /etc/helm/secrets; при переносе Hermes в
контейнер этот путь придётся поменять на /run/secrets, как уже было
найдено для helm-core).

НАЙДЕНО следом за переездом на настоящий PluginManager: колбэки здесь
обязаны быть СИНХРОННЫМИ. ``PluginManager._invoke_hook_callback`` (см.
исходник) делает ровно ``return callback(**payload)`` — без единой
проверки на корутину и без await. Первая версия этого файла (ещё под
именем handler.py, ``async def handle``) на живом Telegram-сообщении не
падала и не гейтила — просто создавала объект корутины, который никто
не запускал (``RuntimeWarning: coroutine 'handle' was never awaited``),
а `pre_gateway_dispatch` в gateway/run.py получал этот объект как
non-None ``_result``, проваливал ``isinstance(_result, dict)`` и молча
пропускал сообщение к модели. Поэтому ``handle``/``_on_pre_gateway_dispatch``
ниже — обычные ``def``, а уведомление в Telegram при недоступном Control
Plane идёт через ``asyncio.get_running_loop().create_task(...)``
(fire-and-forget: await внутри синхронного колбэка невозможен, а
``pre_gateway_dispatch`` вызывается прямо в потоке event loop'а, так что
``get_running_loop()`` не падает).

P8.5.7 (вложения, добавлено 30.08.2026): ``MessageEvent.raw_message``
(``gateway/platforms/base.py``, подтверждено чтением исходника — ищи
``_build_message_event`` в ``plugins/platforms/telegram/adapter.py``)
несёт нативный объект ``python-telegram-bot`` — тот же самый, на
котором сам адаптер уже вызывает ``await obj.get_file()`` +
``await file_obj.download_as_bytearray()`` для agentic-чтения чифом.
Отдельный "smallest transport adapter" (ADR-018 по нумерации спеки,
``docs/adr/ADR-102...``) не понадобился — тот же токен, тот же объект,
никакого второго consumer'а апдейтов.

Решение владельца 30.08.2026 (по итогам живого теста, вскрывшего, что
чиф уже читает вложения "на лету" через свой agentic-цикл): вложение
теперь ВСЕГДА уходит в базу знаний через ``pre_gateway_dispatch``
``{"action": "skip"}`` — то же самое gating-свойство, которым Probe
ниже уже коротко замыкает LLM на LOCAL_ANSWER, — chief вложение
напрямую больше не видит. `chat_intake.py` (SHA256/spool/домен-диалог)
живёт в Control Plane, а этот плагин — вне его процесса (свой venv на
хосте), поэтому вызвать эти функции напрямую нельзя: два новых HMAC-
подписанных HTTP-эндпоинта, тот же паттерн, что уже у
``_register_task``/``_probe_local_answer``.

F-260829-25 (31.08.2026): у Telegram, в отличие от MAX, не было способа
узнать постфактум, что сообщение реально дошло до платной модели —
Control Plane видит только ``pre_gateway_dispatch``/``pre_llm_call``,
оба ДО вызова LLM. Живая разведка (три волны, ``scripts/hermes-recon-
post-response-hook*.sh``) нашла реальный, вызываемый хук
``post_llm_call`` (``agent/turn_finalizer.py``, не декоративный, как
каталог ``~/.hermes/hooks/<name>/`` из P8.5.7 выше) с ``user_message``
прямо в payload — см. ``_on_post_llm_call``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request

CONTROL_PLANE_URL = "http://127.0.0.1:8080/internal/inbound"
KNOWLEDGE_PROBE_URL = "http://127.0.0.1:8080/internal/knowledge/probe"
ATTACHMENT_STAGE_URL = "http://127.0.0.1:8080/internal/knowledge/attachment/stage"
ATTACHMENT_RESOLVE_URL = "http://127.0.0.1:8080/internal/knowledge/attachment/resolve"
#: v3.7 P8.5.2.1 (ZIP batch ingest) — тот же HMAC/base64-паттерн, что и
#: у одиночных вложений выше, отдельные эндпоинты (см. helm_core/api/
#: internal.py: POST /internal/knowledge/batches, .../resolve-domain).
BATCH_STAGE_URL = "http://127.0.0.1:8080/internal/knowledge/batches"
BATCH_RESOLVE_URL = "http://127.0.0.1:8080/internal/knowledge/batches/resolve-domain"
#: P8.5.12 Micro-Memory «Запомни» — тот же HMAC-паттерн (см.
#: helm_core/api/internal.py: POST /internal/knowledge/remember).
KNOWLEDGE_REMEMBER_URL = "http://127.0.0.1:8080/internal/knowledge/remember"
KNOWLEDGE_ADMIN_URL = "http://127.0.0.1:8080/internal/knowledge/admin"
#: F-260829-25 — тот же HMAC-паттерн (см. helm_core/api/internal.py:
#: POST /internal/knowledge/paid-escalation).
KNOWLEDGE_PAID_ESCALATION_URL = "http://127.0.0.1:8080/internal/knowledge/paid-escalation"
HMAC_SECRET_PATH = "/etc/helm/secrets/hermes_service_hmac"
REQUEST_TIMEOUT = 5
#: §14.5.1 "bounded size" — тот же потолок, что уже применяется на
#: стороне Control Plane (chat_intake.MAX_ATTACHMENT_BYTES); проверка
#: здесь просто экономит скачивание заведомо слишком большого файла,
#: финальное решение — всё равно на стороне Control Plane.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

#: Живёт только в памяти процесса гейтвея — переживает один запуск, не
#: рестарт. Смысл этого кэша — донести task_id из pre_gateway_dispatch до
#: pre_llm_call в рамках той же обработки сообщения, не более.
_task_ids: dict[str, str] = {}


def _read_secret() -> str:
    with open(HMAC_SECRET_PATH, encoding="utf-8") as f:
        return f.read().strip()


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    material = timestamp.encode("utf-8") + b"\x00" + body
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def _register_task(channel: str, external_message_id: str, owner_id: str, text: str) -> dict:
    body_obj = {
        "channel": channel,
        "external_message_id": external_message_id,
        "owner_id": owner_id,
        "text": text,
    }
    body = json.dumps(body_obj).encode("utf-8")
    ts = str(time.time())
    sig = _sign(_read_secret(), ts, body)
    req = urllib.request.Request(
        CONTROL_PLANE_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Helm-Timestamp": ts,
            "X-Helm-Signature": sig,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        # Тело ответа несёт причину отказа (422 — какое поле не прошло
        # валидацию, 403 — не тот owner_id) — без него exc сам по себе
        # говорит только код статуса, диагностика вслепую.
        raise RuntimeError(
            f"HTTP {exc.code} от Control Plane: {exc.read().decode(errors='replace')}"
        ) from exc


def _log_send_outcome(task: "asyncio.Task") -> None:
    """done_callback для fire-and-forget send() — иначе провал невидим.

    НАЙДЕНО 29.08.2026 на живом тесте: реальный `TelegramAdapter.send()`
    (plugins/platforms/telegram/adapter.py) принимает `chat_id`/`content`,
    а не `chat_id`/`text` — вызов с `text=` падал `TypeError` в момент
    создания корутины, и голый `except Exception: pass` вокруг
    `create_task(...)` проглатывал её без единой строки в логе. Задача
    выглядела зарегистрированной и Probe отвечал 200, а сообщение
    владельцу так никогда и не приходило. `add_done_callback` — не
    декоративная надстройка, а единственный способ увидеть эту ошибку
    вообще, раз await здесь невозможен (синхронный колбэк).
    """
    try:
        result = task.result()
    except Exception as exc:
        print(f"[helm-control] send() упал: {exc!r}", flush=True)
        return
    if not getattr(result, "success", True):
        print(f"[helm-control] send() вернул неуспех: {result!r}", flush=True)


def _send_reply(gateway, source, content: str) -> None:
    """Fire-and-forget ответ владельцу — колбэк синхронный, await недоступен."""
    try:
        task = asyncio.get_running_loop().create_task(
            gateway.adapters[source.platform].send(chat_id=source.chat_id, content=content)
        )
        task.add_done_callback(_log_send_outcome)
    except Exception as exc:
        print(f"[helm-control] не удалось создать задачу send(): {exc!r}", flush=True)


def _stage_attachment(channel: str, data_base64: str, original_filename: str | None,
                      mime_type: str | None, caption: str | None) -> dict:
    """POST /internal/knowledge/attachment/stage — см. модуль internal.py
    в Control Plane. Fail-closed, как `_register_task`: если Control
    Plane недоступен, исключение уходит вызывающей стороне — вложение
    без подтверждённого spool не считается принятым."""
    body = json.dumps({
        "channel": channel, "data_base64": data_base64,
        "original_filename": original_filename, "mime_type": mime_type, "caption": caption,
    }).encode("utf-8")
    ts = str(time.time())
    sig = _sign(_read_secret(), ts, body)
    req = urllib.request.Request(
        ATTACHMENT_STAGE_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Helm-Timestamp": ts,
                "X-Helm-Signature": sig},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _resolve_attachment(channel: str, reply_text: str, recipient: str | None) -> dict | None:
    """POST /internal/knowledge/attachment/resolve. Fail-OPEN, как
    `_probe_local_answer`: диалог вложений — не проверка допуска, при
    недоступности Control Plane сообщение просто идёт обычным путём
    (`not_pending`-подобное поведение), не блокируется."""
    body = json.dumps({
        "channel": channel, "reply_text": reply_text, "recipient": recipient,
    }).encode("utf-8")
    ts = str(time.time())
    sig = _sign(_read_secret(), ts, body)
    req = urllib.request.Request(
        ATTACHMENT_RESOLVE_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Helm-Timestamp": ts,
                "X-Helm-Signature": sig},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print(f"[helm-control] knowledge_attachment_resolve failed: {exc}", flush=True)
        return None


def _stage_batch(channel: str, data_base64: str, original_filename: str | None,
                 mime_type: str | None, recipient: str | None) -> dict:
    """POST /internal/knowledge/batches. Fail-closed, как `_stage_
    attachment()` — архив без подтверждённого сохранения на Control
    Plane не считается принятым."""
    body = json.dumps({
        "channel": channel, "data_base64": data_base64,
        "original_filename": original_filename, "mime_type": mime_type,
        "recipient": recipient,
    }).encode("utf-8")
    ts = str(time.time())
    sig = _sign(_read_secret(), ts, body)
    req = urllib.request.Request(
        BATCH_STAGE_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Helm-Timestamp": ts,
                "X-Helm-Signature": sig},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _resolve_batch(channel: str, reply_text: str) -> dict | None:
    """POST /internal/knowledge/batches/resolve-domain. Fail-open, как
    `_resolve_attachment()` — недоступность Control Plane не блокирует
    обычное сообщение, оно просто идёт своим путём."""
    body = json.dumps({"channel": channel, "reply_text": reply_text}).encode("utf-8")
    ts = str(time.time())
    sig = _sign(_read_secret(), ts, body)
    req = urllib.request.Request(
        BATCH_RESOLVE_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Helm-Timestamp": ts,
                "X-Helm-Signature": sig},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print(f"[helm-control] knowledge_batches_resolve_domain failed: {exc}", flush=True)
        return None


def _try_remember(channel: str, text: str, origin_message_id: str | None) -> dict:
    """POST /internal/knowledge/remember. Fail-CLOSED, в отличие от
    `_probe_local_answer()`/`_resolve_attachment()`: если Control Plane
    недоступен, сообщение НЕ должно тихо провалиться дальше к chief —
    владелец решил бы, что "Запомни ..." сохранено (LLM вежливо
    подтвердит), а на самом деле ничего не записано. Поднимает
    исключение при сбое — вызывающая сторона обязана сообщить об ошибке,
    не продолжать обычный путь."""
    body = json.dumps({
        "channel": channel, "text": text, "origin_message_id": origin_message_id,
    }).encode("utf-8")
    ts = str(time.time())
    sig = _sign(_read_secret(), ts, body)
    req = urllib.request.Request(
        KNOWLEDGE_REMEMBER_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Helm-Timestamp": ts,
                "X-Helm-Signature": sig},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


#: P8.5.12 — тот же критерий, что helm_core.knowledge.memory.
#: detect_remember_command() на стороне Control Plane (тот же принцип
#: дублирования, что _is_zip_attachment ниже: этот процесс не может
#: импортировать helm_core напрямую). Точное отсечение payload'а делает
#: сервер — здесь достаточно решить, стоит ли вообще звать
#: /internal/knowledge/remember (и, если звать, считать сбой fail-closed).
_REMEMBER_PREFIX_RE = re.compile(
    r"^\s*(?:/remember\b|запомни(?:те)?\b|сохрани(?:те)?\s+в\s+память\b|не\s+забудь(?:те)?\b)",
    re.IGNORECASE,
)


def _looks_like_remember_command(text: str) -> bool:
    return bool(_REMEMBER_PREFIX_RE.match(text))


#: §14.16 — тот же критерий, что helm_core.knowledge.admin.
#: detect_admin_command() на стороне Control Plane; здесь достаточно
#: решить, стоит ли вообще звать сервер. Якорь на начало обязателен:
#: «Не забудь купить молоко» — это «запомни», а не «забудь».
_ADMIN_PREFIX_RE = re.compile(
    r"^\s*(?:удали\s+(?:это\s+)?навсегда|сотри\s+(?:это\s+)?навсегда"
    r"|верни(?:те)?\s+в\s+память|восстанови(?:те)?"
    r"|забудь(?:те)?|не\s+используй|исправь(?:те)?)\b",
    re.IGNORECASE,
)


def _looks_like_admin_command(text: str) -> bool:
    return bool(_ADMIN_PREFIX_RE.match(text))


def _try_admin_command(text: str) -> dict:
    """POST /internal/knowledge/admin. Fail-CLOSED по той же причине, что
    и «Запомни»: половина этих команд необратима, и «модель вежливо
    подтвердила, а на деле ничего не произошло» здесь хуже, чем явная
    ошибка."""
    body = json.dumps({"text": text}).encode("utf-8")
    ts = str(time.time())
    sig = _sign(_read_secret(), ts, body)
    req = urllib.request.Request(
        KNOWLEDGE_ADMIN_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Helm-Timestamp": ts,
                "X-Helm-Signature": sig},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


#: §14.4.0: "ZIP must no longer be treated as a MarkItDown document
#: format" — тот же критерий, что helm_core.knowledge.batch_intake.
#: is_zip_attachment() на стороне Control Plane. Этот процесс живёт вне
#: пакета helm_core (свой venv на хосте Hermes) и не может импортировать
#: её напрямую — критерий продублирован, держать в синхроне вручную при
#: изменении одной из двух копий.
_ZIP_MIME_TYPES = {"application/zip", "application/x-zip-compressed", "application/x-zip"}


def _is_zip_attachment(filename: str | None, mime_type: str | None) -> bool:
    if mime_type in _ZIP_MIME_TYPES:
        return True
    return bool(filename) and filename.lower().endswith(".zip")


def _attachment_is_zip(message) -> bool:
    """Только `document` может быть ZIP — photo/voice/audio/video нет
    смысла проверять вовсе."""
    doc = getattr(message, "document", None)
    if doc is None:
        return False
    return _is_zip_attachment(getattr(doc, "file_name", None), getattr(doc, "mime_type", None))


def _message_has_attachment(message) -> bool:
    """Только document/photo/voice/audio/video поддерживаются P8.5.7 —
    то же подмножество, что уже умеет adapter.py для agentic-чтения.
    `getattr(..., None)` — не прямой атрибут: `raw_message` несёт
    объект, специфичный для платформы адаптера (`MessageEvent` — общая
    "normalized representation, that all adapters produce", но тип этого
    поля отличается по платформам), прямой `message.document` уронил бы
    AttributeError на любой не-Telegram платформе Hermes."""
    if message is None:
        return False
    return bool(getattr(message, "document", None) or getattr(message, "photo", None)
               or getattr(message, "voice", None) or getattr(message, "audio", None)
               or getattr(message, "video", None))


async def _download_message_attachment(message):
    """(bytes, filename, mime_type) для одного из поддерживаемых типов
    вложения, или None. Скачивание — `await obj.get_file()` +
    `await file_obj.download_as_bytearray()`, дословно тот же вызов, что
    уже подтверждён живым чтением `adapter.py` (agentic-чтение чифом,
    строки ~9959-10195 на 30.08.2026) — не гипотеза, воспроизведение
    существующего, работающего в этом же процессе кода.

    Проверка размера ДО скачивания — там, где Telegram отдаёт
    `file_size` заранее (document/audio/video; photo/voice его не всегда
    несут) — экономит трафик на заведомо слишком большом файле; финальное
    решение всё равно на стороне Control Plane (`AttachmentTooLarge`).
    """
    if message.document:
        obj, filename, mime_type = (
            message.document,
            message.document.file_name or f"document_{message.document.file_unique_id}",
            message.document.mime_type,
        )
    elif message.photo:
        largest = message.photo[-1]
        obj, filename, mime_type = largest, f"photo_{largest.file_unique_id}.jpg", "image/jpeg"
    elif message.voice:
        obj = message.voice
        filename = f"voice_{obj.file_unique_id}.ogg"
        mime_type = obj.mime_type or "audio/ogg"
    elif message.audio:
        obj = message.audio
        filename = obj.file_name or f"audio_{obj.file_unique_id}"
        mime_type = obj.mime_type
    elif message.video:
        obj = message.video
        filename = obj.file_name or f"video_{obj.file_unique_id}.mp4"
        mime_type = obj.mime_type
    else:
        return None

    file_size = getattr(obj, "file_size", None)
    if file_size is not None and file_size > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"вложение {file_size} байт превышает лимит {MAX_ATTACHMENT_BYTES}")

    file_obj = await obj.get_file()
    data = bytes(await file_obj.download_as_bytearray())
    return data, filename, mime_type


async def _handle_attachment_async(event, gateway, source, channel: str) -> None:
    """Фоновая задача (fire-and-forget, запущена из синхронного
    `pre_gateway_dispatch`): скачать вложение, отдать в Control Plane,
    ответить владельцу меню доменов. Ничего здесь не блокирует LLM —
    `{"action": "skip"}` уже вернулся вызывающей стороне синхронно, до
    того как эта задача вообще началась."""
    try:
        result = await _download_message_attachment(event.raw_message)
    except Exception as exc:
        print(f"[helm-control] не удалось скачать вложение: {exc!r}", flush=True)
        _send_reply(gateway, source,
                   "Не смог скачать вложение — попробуйте прислать ещё раз.")
        return
    if result is None:
        return  # message_type != TEXT, но не document/photo/voice/audio/video — не наш случай
    data, filename, mime_type = result

    caption = getattr(event.raw_message, "caption", None) or (event.text or None)
    try:
        staged = _stage_attachment(channel, base64.b64encode(data).decode("ascii"),
                                   filename, mime_type, caption)
    except Exception as exc:
        print(f"[helm-control] stage_attachment failed: {exc!r}", flush=True)
        _send_reply(gateway, source,
                   "Не получилось сохранить вложение — попробуйте ещё раз.")
        return
    if staged.get("text"):
        _send_reply(gateway, source, staged["text"])


async def _handle_batch_attachment_async(event, gateway, source, channel: str) -> None:
    """ZIP-вариант `_handle_attachment_async()` — §14.4.0: контейнер
    перехватывается раньше одиночного диалога вложений целиком, но
    скачивание — тот же `_download_message_attachment()` (`document` —
    тот же `get_file()`-путь, что и для одиночного файла). Пред-проверка
    размера внутри неё — `MAX_ATTACHMENT_BYTES` (20MB): это НЕ наш
    собственный лимит на архив (`MAX_ARCHIVE_BYTES=1GB` на стороне
    Control Plane), а реальное ограничение самого Telegram Bot API —
    обычный бот (не self-hosted Local Bot API Server) не отдаёт `getFile`
    крупнее 20MB вообще, независимо от того, что настроено у нас."""
    try:
        result = await _download_message_attachment(event.raw_message)
    except Exception as exc:
        print(f"[helm-control] не удалось скачать архив: {exc!r}", flush=True)
        _send_reply(gateway, source, "Не смог скачать архив — попробуйте прислать ещё раз.")
        return
    if result is None:
        return
    data, filename, mime_type = result

    recipient = str(source.chat_id) if source and source.chat_id is not None else None
    try:
        staged = _stage_batch(channel, base64.b64encode(data).decode("ascii"),
                              filename, mime_type, recipient)
    except Exception as exc:
        print(f"[helm-control] stage_batch failed: {exc!r}", flush=True)
        _send_reply(gateway, source, "Не получилось сохранить архив — попробуйте ещё раз.")
        return
    if staged.get("text"):
        _send_reply(gateway, source, staged["text"])


def _probe_local_answer(text: str) -> dict | None:
    """Free-first Knowledge Probe (ТЗ §14.11, v3.4), ДО обращения к LLM.

    В отличие от `_register_task` это НЕ fail-closed гейт: probe — способ
    сэкономить на платной модели, а не проверка допуска. Недоступность
    Control Plane здесь не блокирует ответ владельцу — сообщение просто
    идёт к LLM как обычно, без бесплатного локального пути в этот раз.
    """
    body = json.dumps({"query": text}).encode("utf-8")
    ts = str(time.time())
    sig = _sign(_read_secret(), ts, body)
    req = urllib.request.Request(
        KNOWLEDGE_PROBE_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Helm-Timestamp": ts,
            "X-Helm-Signature": sig,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print(f"[helm-control] knowledge_probe failed: {exc}", flush=True)
        return None


def _log_paid_escalation(channel: str, text: str) -> None:
    """POST /internal/knowledge/paid-escalation. Fail-open, как
    `_probe_local_answer()`: это метрика (§14.14), не гейт — пропавшая
    запись не должна ронять сам ответ владельцу или мешать ему."""
    body = json.dumps({"channel": channel, "text": text}).encode("utf-8")
    ts = str(time.time())
    sig = _sign(_read_secret(), ts, body)
    req = urllib.request.Request(
        KNOWLEDGE_PAID_ESCALATION_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Helm-Timestamp": ts,
                "X-Helm-Signature": sig},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT):
            pass
    except Exception as exc:
        print(f"[helm-control] knowledge_paid_escalation failed: {exc}", flush=True)


def _on_post_llm_call(**kwargs) -> None:
    """F-260829-25 — §14.14 paid-avoidance metric для Telegram.

    НАЙДЕНО живой разведкой 31.08.2026 (три волны, scripts/hermes-recon-
    post-response-hook*.sh): `post_llm_call` — реальный, вызываемый хук
    (`agent/turn_finalizer.py`), не декоративный, как каталог
    `~/.hermes/hooks/<name>/` из P8.5.7. Вызывается ОДИН РАЗ ЗА ХОД,
    СРАЗУ ПОСЛЕ настоящего ответа модели, с `session_id`, `task_id`,
    `user_message` (оригинальный текст) и `platform` прямо в payload —
    отдельного кэша текста не нужно, в отличие от `_task_ids` для
    `pre_llm_call`.

    Сам факт вызова уже значит платную эскалацию: если `_probe_local_
    answer()` в `pre_gateway_dispatch` вернул LOCAL_ANSWER, тот вернул
    `{"action": "skip"}` и ход до LLM не дошёл — `post_llm_call` для
    него не сработает вовсе. Ровно то же условие, что уже проверяет
    `/hooks/max` перед логированием (`_run_chief_and_enqueue_reply()`),
    только там оно верно по построению (Control Plane сам зовёт Hermes
    синхронно), а здесь подтверждено тем, что этот хук вообще добежал.

    Не привязан к MAX: тот не идёт через Hermes-gateway (Control Plane
    вызывает `/v1/responses` напрямую и логирует сам в `hooks.py`) —
    `post_llm_call` для него просто не должен сработать, но платформа
    всё равно проверяется явно, а не молчаливым предположением.
    """
    if str(kwargs.get("platform") or "").lower() != "telegram":
        return
    text = kwargs.get("user_message")
    if not text:
        return
    _log_paid_escalation("telegram", text)


def handle(event=None, gateway=None, session_store=None, **kwargs):
    # pre_gateway_dispatch передаёт event+gateway; pre_llm_call — нет.
    if event is not None and gateway is not None:
        return _on_pre_gateway_dispatch(event, gateway)
    return _on_pre_llm_call(kwargs)


def _on_pre_gateway_dispatch(event, gateway):
    source = event.source
    channel = source.platform.value if source and source.platform else "system"

    # P8.5.7: вложение — ВСЕГДА в базу знаний, chief его не видит напрямую
    # (решение владельца 30.08.2026 по итогам живого теста). Проверяется
    # ДО `if not event.text`, потому что document/photo/voice/audio/video
    # в Telegram обычно приходят БЕЗ event.text вовсе (текст сообщения
    # пуст, есть только caption) — старая проверка пропустила бы такое
    # сообщение мимо гейта целиком.
    if _message_has_attachment(event.raw_message):
        # v3.7 §14.4.0: ZIP — контейнер, не документ парсера, перехват
        # раньше одиночного диалога вложений целиком (не после него).
        if _attachment_is_zip(event.raw_message):
            asyncio.get_running_loop().create_task(
                _handle_batch_attachment_async(event, gateway, source, channel)
            )
            return {"action": "skip", "reason": "knowledge_batch_pending"}
        asyncio.get_running_loop().create_task(
            _handle_attachment_async(event, gateway, source, channel)
        )
        return {"action": "skip", "reason": "knowledge_attachment_pending"}

    if not event.text:
        return None

    # НАЙДЕНО на живом тесте: Control Plane отвечал 422 на каждое реальное
    # Telegram-сообщение. Причина — event.message_id у Telegram это int
    # (родной тип платформы), а InboundMessage.external_message_id в
    # Control Plane — str; Pydantic в этом режиме не приводит int к str
    # молча. str() ниже — не форматирование ради вкуса, а обязательное
    # приведение типа перед отправкой.
    # НАЙДЕНО на живом тесте: event.user_id у реальных Telegram-сообщений
    # в этой версии Hermes — None (422 "owner_id: String should have at
    # least 1 character"). В приватном чате Telegram chat_id — это тот же
    # числовой id пользователя, что и user_id, поэтому это не подмена
    # identity, а второй валидный источник того же значения.
    if event.user_id is not None:
        owner_id = str(event.user_id)
    elif source and source.chat_id is not None:
        owner_id = str(source.chat_id)
    else:
        owner_id = ""
    external_message_id = (
        str(event.message_id) if event.message_id
        else channel + ":" + owner_id + ":" + str(time.time())
    )

    # P8.5.12: ДО pending-диалогов домена — "Запомни ..." не должен
    # попасть в них как "неверный ответ, переспрашиваю" (тот же порядок,
    # что в helm_core/api/hooks.py). Fail-CLOSED (см. _try_remember()
    # docstring): если это похоже на команду, но Control Plane недоступен,
    # владелец узнаёт об этом явно, сообщение не проваливается к chief.
    if _looks_like_remember_command(event.text):
        try:
            remember_result = _try_remember(channel, event.text, external_message_id)
        except Exception as exc:
            print(f"[helm-control] knowledge_remember failed: {exc!r}", flush=True)
            _send_reply(gateway, source, "HELM Control Plane недоступен. Не запомнил.")
            return {"action": "skip", "reason": "control_plane_unavailable: " + str(exc)}
        if remember_result.get("status") != "not_command":
            if remember_result.get("text"):
                _send_reply(gateway, source, remember_result["text"])
            return {"action": "skip", "reason": "knowledge_remember_" + remember_result["status"]}

    # §14.16: там же и по той же причине. «Забудь про код домофона» иначе
    # ушло бы в обычный поиск и было бы понято как просьба этот код
    # НАЙТИ — ровно наоборот тому, о чём просили.
    if _looks_like_admin_command(event.text):
        try:
            admin_result = _try_admin_command(event.text)
        except Exception as exc:
            print(f"[helm-control] knowledge_admin failed: {exc!r}", flush=True)
            _send_reply(gateway, source, "HELM Control Plane недоступен. Ничего не изменил.")
            return {"action": "skip", "reason": "control_plane_unavailable: " + str(exc)}
        if admin_result.get("status") != "not_command":
            if admin_result.get("text"):
                _send_reply(gateway, source, admin_result["text"])
            return {"action": "skip", "reason": "knowledge_admin_" + admin_result["status"]}

    # P8.5.7 шаг 2: если на этом канале есть неразрешённое вложение, это
    # текстовое сообщение — ответ на диалог выбора домена (номер/имя/
    # алиас/"отмена"), не обычный вопрос владельца. fail-open: как и
    # Probe ниже, недоступность Control Plane не блокирует сообщение —
    # оно просто идёт обычным путём (`not_pending`-подобное поведение).
    recipient = str(source.chat_id) if source and source.chat_id is not None else None
    attachment_result = _resolve_attachment(channel, event.text, recipient)
    if attachment_result and attachment_result.get("status") != "not_pending":
        if attachment_result.get("text"):
            _send_reply(gateway, source, attachment_result["text"])
        return {"action": "skip",
               "reason": "knowledge_attachment_" + attachment_result["status"]}

    # v3.7 P8.5.2.1: тот же fail-open диалог, но для batch — проверяется
    # ПОСЛЕ одиночного вложения (разные таблицы состояния, не могут
    # совпасть, порядок здесь не критичен, но так симметрично тому же
    # порядку в helm_core/api/hooks.py).
    batch_result = _resolve_batch(channel, event.text)
    if batch_result and batch_result.get("status") != "not_pending":
        if batch_result.get("text"):
            _send_reply(gateway, source, batch_result["text"])
        return {"action": "skip", "reason": "knowledge_batch_" + batch_result["status"]}

    try:
        result = _register_task(channel, external_message_id, owner_id, event.text)
    except Exception as exc:
        # print(), не logger: gateway/run.py логирует только reason на INFO,
        # который может быть отфильтрован уровнем логгера самого Hermes.
        # print идёт в stdout напрямую и виден в journalctl независимо от
        # уровня логирования — это то, чего не хватало для диагностики 422.
        print(
            f"[helm-control] register_task failed: channel={channel!r} "
            f"external_message_id={external_message_id!r} owner_id={owner_id!r} "
            f"error={exc}",
            flush=True,
        )
        _send_reply(gateway, source, "HELM Control Plane недоступен. Задача не запущена.")
        return {"action": "skip", "reason": "control_plane_unavailable: " + str(exc)}

    if source and source.chat_id:
        _task_ids[str(source.chat_id)] = result["task_id"]

    probe_result = _probe_local_answer(event.text)
    if probe_result and probe_result.get("outcome") == "LOCAL_ANSWER":
        _send_reply(gateway, source, probe_result["answer_text"])
        return {"action": "skip", "reason": "knowledge_probe_local_answer"}

    return None


def _on_pre_llm_call(kwargs: dict):
    session_id = kwargs.get("session_id")
    task_id = _task_ids.get(str(session_id)) if session_id is not None else None
    if not task_id:
        return None
    return {"context": "HELM_TASK_ID=" + task_id}


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", handle)
    ctx.register_hook("pre_llm_call", handle)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
