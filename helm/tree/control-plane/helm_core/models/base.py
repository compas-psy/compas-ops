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
    owner-facing расписаний (ADR-101) это расхождение в несколько часов.
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


class KnowledgeBatchStatus(enum.StrEnum):
    """v3.7 §14.4.0 — состояние ZIP-архива целиком (не отдельного члена)."""

    RECEIVED = "received"
    HASHING = "hashing"
    WAITING_DOMAIN = "waiting_domain"
    ARCHIVE_PREFLIGHT = "archive_preflight"
    EXPANDING = "expanding"
    QUEUED = "queued"
    PROCESSING = "processing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Батч завершён и больше не может измениться сам по себе — новую работу
#: над ним заводит только явный retry_failed/cancel_remaining.
BATCH_TERMINAL_STATUSES = frozenset({
    KnowledgeBatchStatus.COMPLETED, KnowledgeBatchStatus.COMPLETED_WITH_ERRORS,
    KnowledgeBatchStatus.BLOCKED, KnowledgeBatchStatus.FAILED, KnowledgeBatchStatus.CANCELLED,
})


class KnowledgeBatchItemStatus(enum.StrEnum):
    """v3.7 §14.4.0 — состояние одного члена архива."""

    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    EXACT_DUPLICATE = "exact_duplicate"
    QUARANTINE = "quarantine"
    SKIPPED_UNSUPPORTED = "skipped_unsupported"
    SKIPPED_NESTED_ARCHIVE = "skipped_nested_archive"
    SKIPPED_CANCELLED = "skipped_cancelled"
    FAILED = "failed"


#: §14.4.0: "A batch reaches terminal state only when every batch item is
#: terminal" — QUEUED/PROCESSING не входят.
BATCH_ITEM_TERMINAL_STATUSES = frozenset({
    KnowledgeBatchItemStatus.READY, KnowledgeBatchItemStatus.EXACT_DUPLICATE,
    KnowledgeBatchItemStatus.QUARANTINE, KnowledgeBatchItemStatus.SKIPPED_UNSUPPORTED,
    KnowledgeBatchItemStatus.SKIPPED_NESTED_ARCHIVE, KnowledgeBatchItemStatus.SKIPPED_CANCELLED,
    KnowledgeBatchItemStatus.FAILED,
})


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


class KnowledgeUserRole(enum.StrEnum):
    """v3.8 §14.2 — НЕ multi-owner HELM: SYSTEM_OWNER = существующий
    единственный владелец (полный HELM), KNOWLEDGE_USER = только
    собственный Second Brain, без доступа к остальному HELM."""

    SYSTEM_OWNER = "SYSTEM_OWNER"
    KNOWLEDGE_USER = "KNOWLEDGE_USER"


class KnowledgeUserStatus(enum.StrEnum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class KnowledgeMemoryKind(enum.StrEnum):
    """v3.8 §14.10 — "не создавать десятки схем": kind оптимизирует
    рендер/поиск, canonical_text+payload_json остаются гибкими."""

    FACT = "fact"
    BOOKMARK = "bookmark"
    IDENTIFIER = "identifier"
    NOTE = "note"
    PREFERENCE = "preference"
    TEMPORARY = "temporary"


class KnowledgeMemoryStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    DELETED = "DELETED"


# ── semantic-v2 (v4.0 §14.5-§14.9) ────────────────────────────────────
#
# Отдельный слой от KnowledgeNote/KnowledgeRelation. Те остаются как
# semantic-v1 и по §14.5 «may coexist during rescue»: миграция
# аддитивная и обратимая до R10, старые таблицы не удаляются.


class SemanticNodeKind(enum.StrEnum):
    """§14.5 `knowledge_nodes.kind`.

    Ключевое различие — §14.6: ENTITY это ТОЛЬКО личность («врач
    Безручко»), а EVENT/FACT/DECISION/CONCEPT — утверждения, привязанные
    к источнику. Слияние второго в первое по совпадению имени и есть
    дефект semantic-v1, из-за которого «врач» и «всё, что про врача»
    оказывались одной растущей заметкой.
    """

    ENTITY = "entity"
    EVENT = "event"
    FACT = "fact"
    DECISION = "decision"
    CONCEPT = "concept"
    DOCUMENT_REF = "document_ref"
    MEMORY_REF = "memory_ref"


#: Жизненный цикл `semantic_run_id` по видам узлов. Правило записано
#: здесь, а не выведено из кода: до R2-hardening оно нигде не было
#: сформулировано, и два вида — DOCUMENT_REF/MEMORY_REF — не имели
#: определения вовсе.
#:
#: Узлы-утверждения (EVENT/FACT/DECISION/CONCEPT) порождены конкретным
#: проходом извлечения. Без `semantic_run_id` откат ревизии не знает,
#: что убирать, а запрос не знает, что не показывать, — поэтому
#: обязателен.
#:
#: ENTITY — личность, а не продукт прохода. Один и тот же врач
#: переживает любое число пересборок; привязка к ревизии сделала бы
#: сущность одноразовой и вернула бы дублирование, ради устранения
#: которого v2 и заводится. Поэтому nullable — и `NOT NULL` для ENTITY
#: не вводится намеренно.
#:
#: DOCUMENT_REF/MEMORY_REF — ЯВНОЕ РЕШЕНИЕ 02.09.2026 (R2-hardening).
#: Это узлы-личности существующего источника и существующей микро-
#: памяти: они создаются детерминированно из `knowledge_sources` и
#: `knowledge_memories`, а не извлекаются моделью. Значит их цикл —
#: цикл ENTITY, а не утверждения: `semantic_run_id` nullable,
#: переключение ревизии их не удаляет и не пересоздаёт, живут ровно
#: пока существует то, на что они ссылаются.
#:
#: Почему именно так, а не «как у утверждений»: узел документа — это
#: то, куда указывают рёбра DERIVED_FROM. Сделай его ревизионным — и
#: замена ревизии осиротит все такие рёбра, а повторное создание даст
#: ДВА узла на один документ, то есть ровно ту дублирующуюся личность,
#: которую §14.6 запрещает.
NODE_KINDS_WITHOUT_RUN = frozenset({
    SemanticNodeKind.ENTITY, SemanticNodeKind.DOCUMENT_REF, SemanticNodeKind.MEMORY_REF,
})


class SemanticNodeStatus(enum.StrEnum):
    """§14.5 `knowledge_nodes.status`.

    QUARANTINE — для узлов semantic-v1, которые §14.22 запрещает считать
    каноническими: они не удаляются, но и не участвуют в ответах.
    """

    ACTIVE = "active"
    DISABLED = "disabled"
    SUPERSEDED = "superseded"
    QUARANTINE = "quarantine"
    DELETED = "deleted"


class SemanticDatePrecision(enum.StrEnum):
    """§14.8. Отсутствие точности — не то же самое, что отсутствие даты.

    «в августе» и «19.08.2026» обязаны различаться структурно, иначе
    вопрос «что было в этом году» перестаёт быть запросом к графу. При
    неразрешимой относительной дате спека требует UNKNOWN и сохранение
    текстовой подсказки — выдумывать точную дату запрещено.
    """

    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    UNKNOWN = "unknown"


class SemanticEvidenceType(enum.StrEnum):
    """§14.9 «Evidence semantics».

    Разница между EXTRACTED и OWNER_EXPLICIT — не оттенок: пометить
    машинную связь как написанную владельцем прямо названо нарушением
    (§14.23). Именно это делал semantic-v1, ставя `explicit_link` всему
    подряд.
    """

    OWNER_EXPLICIT = "owner_explicit"
    EXTRACTED = "extracted"
    INFERRED = "inferred"


class SemanticRelationType(enum.StrEnum):
    """§14.9, «Minimum core». Реестр закрыт намеренно.

    Модель не изобретает типы связей: неизвестный тип нормализуется к
    реестру либо становится RELATED_TO с сохранённым свидетельством.
    Доменная специфика живёт в `subtype`/`role`, а не в новых типах —
    иначе через полгода в реестре будет двести значений, половина из них
    синонимы, и обход графа станет невозможным.
    """

    INVOLVES = "involves"
    HAS_ROLE = "has_role"
    ABOUT = "about"
    LOCATED_AT = "located_at"
    PART_OF = "part_of"
    CREATED_BY = "created_by"
    OWNED_BY = "owned_by"
    RESULTED_IN = "resulted_in"
    REASON_FOR = "reason_for"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    DERIVED_FROM = "derived_from"
    REFERS_TO = "refers_to"
    RELATED_TO = "related_to"


class SemanticWindowStatus(enum.StrEnum):
    """§14.4.1 `knowledge_semantic_windows.status`.

    Спека называет три терминальных состояния — PROCESSED, NO_KNOWLEDGE,
    FAILED — и отдельно описывает TRUNCATED: окно, упёршееся в потолок
    атомов, «автоматически делится/перезапускается». Деление здесь не
    состояние, а событие: родитель получает SPLIT и перестаёт быть
    работой, работу несут дети.

    SPLIT сделан терминальным намеренно. «100% окон терминальны» — это
    проверка полноты разбора, и родитель, навсегда застрявший в
    промежуточном состоянии, делал бы её невыполнимой при каждом
    делении.

    PROCESSED против NO_KNOWLEDGE — не оттенок: §14.4.1 требует, чтобы
    «во фрагменте нечего извлекать» отличалось от «модель вернула
    неполный объект, и мы это проглотили». Поэтому у PROCESSED всегда
    есть `result_hash` и счётчики, даже когда узлов ноль.
    """

    PENDING = "pending"
    PROCESSED = "processed"
    NO_KNOWLEDGE = "no_knowledge"
    SPLIT = "split"
    FAILED = "failed"


#: Окно в любом из этих состояний больше не ждёт работы.
TERMINAL_WINDOW_STATUSES = frozenset({
    SemanticWindowStatus.PROCESSED, SemanticWindowStatus.NO_KNOWLEDGE,
    SemanticWindowStatus.SPLIT, SemanticWindowStatus.FAILED,
})


class SemanticRunStatus(enum.StrEnum):
    """§14.5 `knowledge_semantic_runs.status`.

    READY — единственное состояние, при котором ревизия может стать
    текущей для источника. DEGRADED отличается от FAILED тем, что часть
    окон обработана: §14.19 требует показывать это отдельно, а не
    схлопывать в «документ готов».
    """

    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"


#: Реестры semantic-v2 закрыты не только в Python. §14.9 называет реестр
#: связей закрытым, §14.5 перечисляет статусы и виды поимённо — а enum в
#: Python не мешает ни `psql`, ни миграции, ни коду в обход модели
#: положить в колонку что угодно. Проверка в самой базе — то же
#: «defense in depth», что уже применено к тенантности (RLS): не вместо
#: enum, а под ним.
#:
#: Возвращается кусок SQL для CHECK, а не готовый CheckConstraint:
#: одна и та же строка нужна и в public-модели, и в health-зеркале, а
#: имена ограничений у них разные.
def sql_enum_values(enum_cls) -> str:
    """`'a', 'b', 'c'` — значения перечисления для `IN (...)` в CHECK."""
    return ", ".join(f"'{member.value}'" for member in enum_cls)
