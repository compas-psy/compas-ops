"""Регистрация входящих сообщений до первого LLM-вызова (ТЗ §9.3).

Гейт pre-dispatch: Hermes не имеет права начать работу, пока Control Plane
не зарегистрировал задачу (A-DoD п.2, п.3). Поэтому эта функция — не
оптимизация, а точка, без прохождения которой цепочка не продолжается.

Дедупликация различает три случая, и разница между вторым и третьим — не
деталь, а требование §30.2:

1. та же доставка того же сообщения (channel + external_message_id) → одна
   задача. Telegram переотправляет апдейты при разрыве long polling;
2. один и тот же текст, пришедший в Telegram И в MAX → одна задача. MAX —
   fallback (§10), владелец дублирует запрос, когда не уверен, что первый
   дошёл, а не чтобы получить два исполнения;
3. тот же текст, повторённый владельцем в том же канале → ДВЕ задачи.
   Это осознанное «сделай ещё раз», и схлопывать его нельзя.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import ChannelEvent, Task, TaskEvent, TaskStatus, utcnow

#: Окно, внутри которого одинаковый текст из РАЗНЫХ каналов считается одним
#: намерением. Значение выбрано под сценарий «написал в Telegram, не увидел
#: ответа, продублировал в MAX»: это минуты, не часы.
CROSS_CHANNEL_WINDOW = timedelta(minutes=10)

_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Нормализация для сравнения намерений, а не для хранения.

    Схлопывает регистр и пробелы: «Собери отчёт» и «собери  отчёт» —
    одно намерение. Пунктуация сохраняется: «удали всё» и «удали всё?» —
    разные вещи, и последнее — вопрос, а не команда.
    """
    return _WHITESPACE.sub(" ", text.strip()).casefold()


def normalized_hash(owner_id: str, text: str) -> str:
    material = f"{owner_id}\x00{normalize_text(text)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class NotOwner(PermissionError):
    """Сообщение не от владельца — задача не заводится (§30.2)."""


@dataclass
class IngestResult:
    task: Task
    created: bool
    #: Почему задача не создана заново — для audit и для отладки дедупликации.
    dedup_reason: str | None = None


class IngestService:
    def __init__(self, session: Session, owner_id: str):
        self.session = session
        self.owner_id = owner_id

    def register(self, *, channel: str, external_message_id: str, owner_id: str,
                 text: str, title_redacted: str | None = None) -> IngestResult:
        """Зарегистрировать входящее. Вызывается ДО любого обращения к модели."""
        if owner_id != self.owner_id:
            raise NotOwner(f"отправитель {owner_id!r} не владелец")

        now = utcnow()
        n_hash = normalized_hash(owner_id, text)

        # (1) Та же доставка того же сообщения.
        same_delivery = self.session.scalar(
            select(ChannelEvent).where(
                ChannelEvent.channel == channel,
                ChannelEvent.external_message_id == external_message_id,
            )
        )
        if same_delivery is not None and same_delivery.task_id is not None:
            return IngestResult(self.session.get(Task, same_delivery.task_id),
                                created=False, dedup_reason="same_external_message_id")

        # (2) Тот же текст из другого канала в пределах окна.
        cross = self.session.scalar(
            select(ChannelEvent)
            .where(
                ChannelEvent.owner_id == owner_id,
                ChannelEvent.normalized_hash == n_hash,
                ChannelEvent.channel != channel,
                ChannelEvent.received_at >= now - CROSS_CHANNEL_WINDOW,
                ChannelEvent.task_id.is_not(None),
            )
            .order_by(ChannelEvent.received_at.desc())
        )
        if cross is not None:
            self._record_event(channel, external_message_id, owner_id, n_hash, cross.task_id)
            task = self.session.get(Task, cross.task_id)
            self.session.add(TaskEvent(
                task_id=task.id, actor="control-plane", event_type="task.cross_channel_duplicate",
                payload_redacted={"channel": channel, "original_channel": cross.channel},
            ))
            return IngestResult(task, created=False, dedup_reason="cross_channel_duplicate")

        # (3) Всё остальное — новая задача, включая намеренный повтор
        #     владельца в том же канале.
        task = Task(
            origin_channel=channel,
            origin_message_id=external_message_id,
            origin_owner_id=owner_id,
            normalized_hash=n_hash,
            status=TaskStatus.REGISTERED,
            title_redacted=title_redacted,
        )
        self.session.add(task)
        self.session.flush()
        self._record_event(channel, external_message_id, owner_id, n_hash, task.id)
        self.session.add(TaskEvent(
            task_id=task.id, actor="control-plane", event_type="task.registered",
            payload_redacted={"channel": channel},
        ))
        return IngestResult(task, created=True)

    def _record_event(self, channel: str, external_message_id: str, owner_id: str,
                      n_hash: str, task_id: uuid.UUID) -> None:
        self.session.add(ChannelEvent(
            channel=channel, external_message_id=external_message_id, owner_id=owner_id,
            normalized_hash=n_hash, task_id=task_id,
        ))
        try:
            self.session.flush()
        except IntegrityError:
            # Гонка одновременной доставки того же сообщения: запись уже есть.
            self.session.rollback()
