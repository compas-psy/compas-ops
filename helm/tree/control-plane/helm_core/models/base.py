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
    в неизвестном часовом поясе — на сервере с Europe/Moscow для
    owner-facing расписаний (ADR-019) это расхождение в несколько часов.
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


class KnowledgeDomain(enum.StrEnum):
    """Namespaces HELM Knowledge (ТЗ §14.15). Список закрытый: новый домен —
    решение владельца, не то, что заводится на лету произвольной строкой."""

    PERSONAL = "personal"
    HEALTH = "health"
    SIMPAS_COMPANY = "simpas/company"
    SIMPAS_PRACTICE = "simpas/practice"
    SIMPAS_ZAPISKI = "simpas/zapiski"
    SIMPAS_MOMENTS = "simpas/moments"
    PSY_MARKETING = "psy-marketing"
    VENTURES = "ventures"
    ENGINEERING = "engineering"
    SIGNALAI_DOCS = "signalai-docs"
    #: Добавлено решением владельца 29.08.2026: внешняя справочная
    #: литература (книги по психологии и т.п.), отдельно от
    #: SIMPAS_PRACTICE — та про рабочую практику самого SIMPAS, не про
    #: библиотеку сторонних источников.
    LIBRARY = "library"


class KnowledgeSensitivity(enum.StrEnum):
    """§14.3 markdown contract: `sensitivity`."""

    PUBLIC = "public"
    INTERNAL = "internal"
    PERSONAL = "personal"
    HEALTH = "health"
    CLIENT_RESTRICTED = "client_restricted"


class KnowledgeTrust(enum.StrEnum):
    """§14.3 markdown contract: `trust`."""

    PRIMARY = "primary"
    EXTRACTED = "extracted"
    OWNER_VERIFIED = "owner_verified"
    INFERRED = "inferred"


class KnowledgeStatus(enum.StrEnum):
    """§14.3 markdown contract: `status`. Общий для sources и notes."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    #: Не из markdown contract — состояние source, у которого и fast path, и
    #: Docling дали неуверенный результат (§14.6 «Parser quality gate»):
    #: «не создавать уверенные knowledge facts» на таком материале.
    NEEDS_REVIEW = "needs_review"


class KnowledgeIngestStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class KnowledgeAnswerMode(enum.StrEnum):
    """§14.12 zero-paid answer modes."""

    Z0 = "Z0"
    Z1 = "Z1"
    Z2 = "Z2"
    C1 = "C1"
