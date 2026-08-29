"""Канал MAX (ТЗ §10): разбор входящего вебхука и отправка исходящего.

Здесь ровно три вещи и ничего больше: проверка секрета вебхука, разбор
события `message_created` и HTTP-вызов MAX Bot API. Ни регистрации задач,
ни дедупликации, ни обращения к Hermes — это делают вызывающие
(`api/hooks.py`, `dispatch.py`), и разделение нужно, чтобы разбор чужого
формата тестировался без БД и без сети.

ПРОВЕРЕНО ЖИВЬЁМ 29.08.2026 (scripts/max-diagnose-send.sh, реальный бот,
реальный `chat_id` из живого вебхука):

* база `https://platform-api2.max.ru`;
* токен идёт в заголовке `Authorization`;
* отправка — `POST /messages?chat_id=...`, JSON-тело `{"text": ...}`.

Подтвердилось именно то, о чём предупреждала документация «TamTam Bot
API» — прямого наследника MAX: `chat_id` идёт query-параметром, а НЕ
полем тела. Первая версия этого файла (по документации самого MAX,
без живого бота) клала `chat_id` в тело вместе с `text` — MAX принимал
такой запрос синтаксически (без ошибки схемы), но отвечал `400
Unknown recipient`, потому что тело для маршрутизации не читает вовсе.
"""

from __future__ import annotations

import hmac
import json
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API_BASE = "https://platform-api2.max.ru"

#: Связка корня и промежуточного сертификата Минцифры, которую кладёт
#: scripts/install-ru-ca.sh и монтирует docker-compose. Сертификат
#: platform-api2.max.ru выдан «Russian Trusted Sub CA», которого нет в
#: стандартном наборе Debian, — без этой связки любая отправка падает на
#: проверке TLS («unable to get local issuer certificate»).
RU_CA_BUNDLE = Path("/etc/ssl/ru-ca/russian-trusted.pem")

#: §10.1: «Control Plane проверяет X-Max-Bot-Api-Secret».
WEBHOOK_SECRET_HEADER = "X-Max-Bot-Api-Secret"

REQUEST_TIMEOUT_SECONDS = 10


def verify_webhook_secret(expected: str, provided: str | None) -> bool:
    """Сравнение секрета вебхука за постоянное время.

    .encode() обязателен на обеих сторонах: compare_digest роняет
    TypeError (не False) на не-ASCII в str-аргументе, а `provided`
    приходит прямо из заголовка запроса — см. F-260829-05.
    """
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


@dataclass(frozen=True)
class InboundMax:
    """Входящее сообщение MAX, приведённое к тому, что нужно Control Plane.

    `text` — None для сообщения, состоящего только из вложения (P8.5.7):
    раньше такое сообщение просто отбрасывалось (`not text` был частью
    условия отказа), значит регрессии для существующего пути нет — просто
    появился новый валидный случай, которого раньше не пропускала сама
    функция разбора.
    """

    text: str | None
    sender_id: str
    chat_id: str
    message_id: str
    #: Сырые элементы `message.body.attachments`, ещё не разобранные —
    #: `parse_attachment()` ниже. Пустой список для обычного текстового
    #: сообщения (default, не None — `attachments or []` в вызывающем коде
    #: не понадобится).
    attachments: list[dict] = field(default_factory=list)


def parse_message_created(update: dict) -> InboundMax | None:
    """Разобрать событие вебхука. None — событие не наше или неполное.

    Возвращать None, а не бросать: MAX присылает на один и тот же URL все
    типы подписки (`bot_added`, `message_edited` и прочие), и отвечать на
    них ошибкой — значит заставлять MAX ретраить то, что мы сознательно
    игнорируем.
    """
    if update.get("update_type") != "message_created":
        return None

    message = update.get("message") or {}
    body = message.get("body") or {}
    sender = message.get("sender") or {}
    recipient = message.get("recipient") or {}

    text = body.get("text")
    sender_id = sender.get("user_id")
    chat_id = recipient.get("chat_id")
    message_id = body.get("mid")
    attachments = body.get("attachments") or []

    # Приведение к str обязательно: id в MAX — числа (как и в Telegram, где
    # это уже стоило одного живого 422 на int message_id, см. helm-control).
    # Сообщение валидно, если есть текст ИЛИ хотя бы одно вложение (P8.5.7)
    # — раньше отсутствие текста само по себе отбрасывало любое сообщение,
    # включая файл без подписи.
    if (not text and not attachments) or sender_id is None or chat_id is None or not message_id:
        return None
    return InboundMax(text=text, sender_id=str(sender_id), chat_id=str(chat_id),
                      message_id=str(message_id), attachments=attachments)


class MaxAttachmentUnsupported(Exception):
    """Payload вложения не совпал с ожидаемой формой (P8.5.7).

    НЕ ПОДТВЕРЖДЕНО ЖИВЬЁМ: `dev.max.ru` недоступен из egress-политики
    песочницы разработки — тот же класс ограничения, что был у
    `huggingface.co` для Docling (P8.5.2). Форма ниже (`payload.url` для
    входящего файла) — по документированному поведению TamTam-производных
    Bot API, не проверена реальным вебхуком. Сообщение исключения несёт
    ИМЕНА полей payload, не значения (там может быть токен) — первый живой
    сбой сразу покажет, чего не хватает, вместо повторного гадания.
    """

    def __init__(self, kind: str, payload_keys: list[str]):
        self.kind = kind
        self.payload_keys = payload_keys
        super().__init__(f"неизвестная форма payload для типа={kind!r}, ключи={payload_keys}")


@dataclass(frozen=True)
class InboundMaxAttachment:
    kind: str
    filename: str | None
    url: str


def parse_attachment(attachment: dict) -> InboundMaxAttachment:
    """Разбор одного элемента `message.body.attachments`. См. предупреждение
    у `MaxAttachmentUnsupported` — форма не подтверждена живым тестом."""
    kind = attachment.get("type", "")
    payload = attachment.get("payload") or {}
    url = payload.get("url")
    if not url:
        raise MaxAttachmentUnsupported(kind, sorted(payload.keys()))
    filename = attachment.get("filename") or payload.get("filename")
    return InboundMaxAttachment(kind=kind, filename=filename, url=url)


def download_attachment(url: str, *, timeout: int = REQUEST_TIMEOUT_SECONDS) -> bytes:
    """Скачать байты уже готового URL вложения (см. `parse_attachment`).

    Корень Минцифры нужен и здесь: если MAX отдаёт вложения со своего же
    домена (или поддомена), сертификат тот же, что у `platform-api2.max.ru`
    (F-260829-19); если реальный CDN окажется на ДРУГОМ домене с обычным
    публичным сертификатом — контекст всё равно доверяет обычным корням,
    добавляя связку Минцифры поверх, а не вместо (см. `ssl_context()`).
    """
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
        return response.read()


def ssl_context() -> ssl.SSLContext:
    """Контекст TLS для вызовов MAX: обычные корни ПЛЮС корень Минцифры.

    Именно плюс, а не вместо: подменять весь набор корней связкой из двух
    сертификатов значило бы перестать доверять всем остальным. И именно
    здесь, а не в доверенных всего контейнера: доверие Минцифры нужно
    ровно для одного адреса, и незачем распространять его на все
    исходящие соединения Control Plane.

    Связки нет — контекст остаётся стандартным. Так код работает в тестах
    и в любой среде без этого файла, а на сервере без связки отправка
    честно падает на проверке сертификата, а не тихо перестаёт проверять.
    """
    context = ssl.create_default_context()
    if RU_CA_BUNDLE.exists():
        context.load_verify_locations(cafile=str(RU_CA_BUNDLE))
    return context


def _send_request(token: str, chat_id: str, text: str, timeout: int) -> None:
    query = urllib.parse.urlencode({"chat_id": chat_id})
    body = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/messages?{query}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": token},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
        response.read()


class MaxSender:
    """Отправка в MAX. Вызывается доставщиком outbox (§10.3).

    Класс, а не функция: доставщику нужен вызываемый объект, уже знающий
    токен, — иначе токен пришлось бы таскать через всю очередь.
    """

    channel = "max"

    def __init__(self, token: str, *, timeout: int = REQUEST_TIMEOUT_SECONDS):
        self._token = token
        self._timeout = timeout

    def __call__(self, recipient: str, text: str) -> None:
        if not self._token:
            raise RuntimeError("токен бота MAX не задан — отправка невозможна")
        _send_request(self._token, recipient, text, self._timeout)
