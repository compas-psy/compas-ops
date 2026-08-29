"""Доставка исходящих из outbox (ТЗ §10.3: Hermes result → outbox → MAX API).

`outbox.enqueue()` кладёт сообщение в очередь, но до сих пор никто её не
разбирал — доставщика в Control Plane не существовало вовсе. Здесь он и
появляется: один общий для всех каналов, потому что канал отличается
только вызываемым объектом-отправителем.

Порядок операций внутри одной попытки выбран так, чтобы падение процесса
в любой момент не давало тихой потери сообщения:

    attempts += 1, next_attempt_at = now + backoff   → COMMIT
    → отправка по сети
    → status = SENT, payload очищен                  → COMMIT

Крах между двумя коммитами оставляет строку PENDING с отложенной
следующей попыткой — сообщение уйдёт повторно (at-least-once). Обратный
порядок (сначала отправка, потом счётчик) при том же крахе дал бы
бесконечный цикл повторных отправок, что для владельца хуже.

Промежуточного статуса SENDING нет намеренно: он потребовал бы отдельной
логики «переклеить зависшие SENDING» после аварийного рестарта — сложность
ради состояния, которое здесь ничего не решает (§2 CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import OutboxMessage, utcnow

#: Задержки перед повторной попыткой по номеру попытки. После исчерпания
#: списка сообщение переводится в FAILED: канал, который не принял
#: сообщение шесть раз за ~час, не примет его и на седьмой, а вечно
#: растущая очередь скрывает проблему вместо того, чтобы её показать.
BACKOFF = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=30),
)

MAX_ATTEMPTS = len(BACKOFF) + 1

#: Отправитель канала: (recipient, text) -> None, исключение = не доставлено.
Sender = Callable[[str, str], None]


@dataclass
class DispatchReport:
    sent: int = 0
    retried: int = 0
    failed: int = 0


def deliver_pending(session: Session, senders: Mapping[str, Sender], *,
                    limit: int = 20) -> DispatchReport:
    """Разобрать очередь один раз. Возвращает отчёт, ничего не печатает."""
    now = utcnow()
    report = DispatchReport()

    messages = session.scalars(
        select(OutboxMessage)
        .where(OutboxMessage.status == "PENDING", OutboxMessage.next_attempt_at <= now)
        .order_by(OutboxMessage.next_attempt_at)
        .limit(limit)
    ).all()

    for message in messages:
        sender = senders.get(message.channel)
        text = (message.payload_reference or {}).get("text")
        if sender is None or not text:
            # Некому доставить или нечего: строка не должна крутиться в
            # очереди вечно, но и молча исчезнуть тоже не должна.
            message.status = "FAILED"
            report.failed += 1
            session.commit()
            continue

        attempt = message.attempts
        message.attempts = attempt + 1
        message.next_attempt_at = now + BACKOFF[min(attempt, len(BACKOFF) - 1)]
        session.commit()

        try:
            sender(message.recipient, text)
        except Exception:
            # Текст ошибки не логируется здесь: в него провайдеры кладут
            # эхо запроса, то есть само сообщение владельца. Причина
            # видна вызывающему по счётчику попыток и статусу.
            if message.attempts >= MAX_ATTEMPTS:
                message.status = "FAILED"
                report.failed += 1
            else:
                report.retried += 1
            session.commit()
            continue

        message.status = "SENT"
        # §10.2: Control Plane не хранит переписку. Доставленный текст
        # больше не нужен ни для чего — очередь помнит только факт доставки.
        message.payload_reference = None
        report.sent += 1
        session.commit()

    return report
