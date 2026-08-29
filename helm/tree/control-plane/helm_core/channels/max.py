"""Канал MAX (ТЗ §10): разбор входящего вебхука и отправка исходящего.

Здесь ровно три вещи и ничего больше: проверка секрета вебхука, разбор
события `message_created` и HTTP-вызов MAX Bot API. Ни регистрации задач,
ни дедупликации, ни обращения к Hermes — это делают вызывающие
(`api/hooks.py`, `dispatch.py`), и разделение нужно, чтобы разбор чужого
формата тестировался без БД и без сети.

ПРОВЕРЕНО ПО ДОКУМЕНТАЦИИ, НЕ ЖИВЬЁМ (29.08.2026; dev.max.ru недоступен
из среды агента, живого бота MAX ещё нет):

* база `https://platform-api2.max.ru` — домен сменился с
  `platform-api.max.ru` 19.07.2026;
* токен идёт в заголовке `Authorization`; передача токена query-параметром
  больше не поддерживается;
* отправка — `POST /messages`, JSON-тело `{"chat_id": ..., "text": ...}`.

Единственное, что здесь может не совпасть с реальностью: MAX унаследован
от TamTam Bot API, где `chat_id` передавался query-параметром, а в теле
оставался только текст. Если живой смоук-тест вернёт 400 с жалобой на
отсутствующий chat_id — правка ровно в одной строке `_send_request`
(перенести chat_id в query), остальной код не затрагивается.
"""

from __future__ import annotations

import hmac
import json
import urllib.request
from dataclasses import dataclass

API_BASE = "https://platform-api2.max.ru"

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
    """Входящее сообщение MAX, приведённое к тому, что нужно Control Plane."""

    text: str
    sender_id: str
    chat_id: str
    message_id: str


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

    # Приведение к str обязательно: id в MAX — числа (как и в Telegram, где
    # это уже стоило одного живого 422 на int message_id, см. helm-control).
    if not text or sender_id is None or chat_id is None or not message_id:
        return None
    return InboundMax(text=text, sender_id=str(sender_id),
                      chat_id=str(chat_id), message_id=str(message_id))


def _send_request(token: str, chat_id: str, text: str, timeout: int) -> None:
    body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/messages",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": token},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
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
