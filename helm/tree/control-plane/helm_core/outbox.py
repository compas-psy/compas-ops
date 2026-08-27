"""Исходящие сообщения ровно один раз (ТЗ §7.2, §30.2 «outbox no duplicate»).

Дедупликация — на UNIQUE(dedup_key) в БД, а не на проверке «а не отправляли
ли мы уже». Проверка чтением проигрывает гонке двух воркеров; ограничение
уникальности — нет.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import OutboxMessage


def dedup_key(channel: str, recipient: str, reference: str) -> str:
    material = f"{channel}\x00{recipient}\x00{reference}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class EnqueueResult:
    message: OutboxMessage
    created: bool


def enqueue(session: Session, *, channel: str, recipient: str, reference: str,
            payload_reference: dict | None = None) -> EnqueueResult:
    """Поставить сообщение в очередь. Повторный вызов не создаёт дубль."""
    key = dedup_key(channel, recipient, reference)
    existing = session.query(OutboxMessage).filter(OutboxMessage.dedup_key == key).one_or_none()
    if existing is not None:
        return EnqueueResult(existing, created=False)

    message = OutboxMessage(channel=channel, recipient=recipient, dedup_key=key,
                            payload_reference=payload_reference)
    session.add(message)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        found = session.query(OutboxMessage).filter(OutboxMessage.dedup_key == key).one()
        return EnqueueResult(found, created=False)
    return EnqueueResult(message, created=True)
