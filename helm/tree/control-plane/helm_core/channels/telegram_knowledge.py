"""Канал Dedicated Knowledge Bot (v3.8 §9.0, P8.6.2): разбор входящего
Telegram-вебхука.

Отдельный от owner chief bot: свой Telegram bot token, свой webhook,
идёт НАПРЯМУЮ в Control Plane, минуя Hermes целиком ("This bot is a
transport adapter, not a new reasoning service", §9.0). Здесь ровно
разбор формата — та же дисциплина, что `channels/max.py` (парсинг
чужого формата тестируется без БД и без сети).

Raw Telegram Bot API JSON (обычный HTTPS-вебхук, НЕ python-telegram-bot
объект — этот процесс не подписан на тот же gateway, что чиф) — формат
задокументирован https://core.telegram.org/bots/api#update.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Официальный механизм Telegram для проверки подлинности вебхука —
#: `secret_token`, заданный при `setWebhook`, возвращается ЭТИМ
#: заголовком на каждый вызов (https://core.telegram.org/bots/api#setwebhook).
#: Не тот же заголовок, что у MAX (`X-Max-Bot-Api-Secret`) — разные платформы,
#: разная конвенция именования.
WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


#: P8.6.2 этого захода не включает файлы/ZIP/голос для KNOWLEDGE_USER
#: (нужен отдельный Telegram file-download по raw HTTP — здесь никакой
#: python-telegram-bot нет, `get_file()`/`download_as_bytearray()`
#: недоступны; явный, задокументированный пробел, не тихое игнорирование
#: — вебхук отвечает honest "пока не поддерживается", не молчит).
_ATTACHMENT_KEYS = ("document", "photo", "voice", "audio", "video")


@dataclass(frozen=True)
class InboundKnowledgeTelegram:
    #: Telegram `from.id` — канонический принцип идентичности (§14.3
    #: "Owner-entered chat IDs are not sufficient proof of identity" —
    #: единственное, чему здесь можно доверять, это то, что реально
    #: пришло в теле подписанного вебхука, не то, что владелец ввёл
    #: вручную при создании инвайта).
    external_user_id: str
    #: Для доставки ответа (outbox) — НЕ используется как принцип идентичности.
    external_chat_id: str
    text: str | None
    message_id: str | None
    #: §14.3 "private chat only by default" — групповые чаты не
    #: поддерживаются этим ботом вовсе.
    is_private: bool
    has_attachment: bool


def parse_update(update: dict) -> InboundKnowledgeTelegram | None:
    """None — не наш тип апдейта (не текстовое личное сообщение) или
    апдейт неполный; вызывающая сторона отвечает 200 и ничего не делает
    (тот же принцип, что `parse_message_created()` в `channels/max.py`
    — Telegram не должен получать повод ретраить то, что мы игнорируем
    сознательно)."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    from_ = message.get("from")
    chat = message.get("chat")
    if not isinstance(from_, dict) or not isinstance(chat, dict):
        return None
    user_id = from_.get("id")
    chat_id = chat.get("id")
    if user_id is None or chat_id is None:
        return None
    message_id = message.get("message_id")
    return InboundKnowledgeTelegram(
        external_user_id=str(user_id),
        external_chat_id=str(chat_id),
        text=message.get("text"),
        message_id=str(message_id) if message_id is not None else None,
        is_private=chat.get("type") == "private",
        has_attachment=any(key in message for key in _ATTACHMENT_KEYS),
    )
