"""Общие примитивы модели данных (ТЗ §7.2)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: Явные имена ограничений: без них Alembic на PostgreSQL генерирует
#: автоимена, и миграция, снимающая constraint, не находит его по имени.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk():
    return mapped_column(primary_key=True, default=uuid.uuid4)


def ts_column(**kw):
    """Время всегда с таймзоной.

    Naive timestamp здесь означал бы, что TTL одобрения (§8.4) считается
    в неизвестном часовом поясе — на сервере с Europe/Helsinki для
    owner-facing расписаний (§4.1) это расхождение в два-три часа.
    """
    return mapped_column(DateTime(timezone=True), **kw)


class TaskStatus(enum.StrEnum):
    """§7.5. Порядок объявления — порядок нормального прохода."""

    RECEIVED = "RECEIVED"
    REGISTERED = "REGISTERED"
    CLASSIFIED = "CLASSIFIED"
    RUNNING = "RUNNING"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    VERIFYING = "VERIFYING"
    DONE = "DONE"
    # Ошибочные состояния
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    BLOCKED_CI = "BLOCKED_CI"


class ApprovalStatus(enum.StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    #: Промежуточное состояние между «одобрено» и «исполнено». Существует
    #: ради ровно-однократности (§30.1): переход APPROVED → EXECUTING —
    #: атомарный claim, второй исполнитель его не получит.
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class Channel(enum.StrEnum):
    TELEGRAM = "telegram"
    PANEL = "panel"
    MAX = "max"
    SYSTEM = "system"
