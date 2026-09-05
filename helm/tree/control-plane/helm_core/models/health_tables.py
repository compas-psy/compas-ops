"""Схема `health` — sidecar с чувствительными полями health-источников,
физически отдельная роль БД (ТЗ §4.5, §6.5, ADR-005).

Модель — "generic public envelope + security-scope private payload",
не полное зеркало `KnowledgeSource` в отдельной схеме (решение
владельца при разборе P12, см. ADR-005): `public.knowledge_sources`
остаётся ЕДИНОЙ таблицей-конвертом для всех доменов, включая health —
через неё же работает единая очередь `knowledge_ingest_jobs` и вся
fair-queue/retry-логика `worker.py`, без дублирования. Для health-строк
в конверте НЕТ ни `original_filename`, ни `raw_path`, ни `mime_type`,
ни `parser` — эти поля физически чувствительны (имя файла вроде
«Консультация уролога.pdf» — уже медицинская информация, не только
содержимое) и живут ЗДЕСЬ, в `health.knowledge_source_private`,
доступной только роли `helm_health`. `sha256` остаётся в конверте
(нужен для дедупа синхронным путём `register_file_for_ingest()`,
который решает "уже видели этот файл" ДО того, как health-сессия
вообще создана) — открытый хэш не раскрывает ни имя, ни содержимое
файла.

Собственный `DeclarativeBase`, не `Base` из `base.py`: `migrations/
env.py::target_metadata = Base.metadata`, и `helm_app` (которым Alembic
подключается) не имеет CREATE на схему `health` — таблицами этой схемы
управляет `scripts/setup-health-role.sh` (ручной, идемпотентный
шаг, тот же класс исключения, что уже есть у `compose/post-migration.
sql`), не `alembic upgrade head`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint, ForeignKey, Index, Integer, MetaData, Numeric, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .base import (
    NAMING_CONVENTION, EntityIdentityMatch, EntityResolutionReason,
    EntityResolutionStatus, KnowledgeStatus, SemanticDatePrecision, SemanticEvidenceType,
    SemanticNodeKind, SemanticNodeStatus, SemanticRelationType, SemanticWindowStatus,
    ts_column, utcnow, sql_enum_values,
)
from .tables import _ATOM_KINDS_SQL, _KINDS_WITHOUT_RUN_SQL, KNOWLEDGE_EMBED_DIM


class HealthBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION, schema="health")


class HealthKnowledgeSourcePrivate(HealthBase):
    """Единственное реально чувствительное поле health-source — имя
    файла (`original_filename`, само по себе медицинская информация,
    напр. «Консультация уролога.pdf»). `raw_path` (хэш-производное имя,
    `{sha256}.<ext>`), `mime_type`, `parser` НЕ переехали сюда — они не
    идентифицируют документ и остаются в `public.knowledge_sources`, как
    у любого другого домена (см. докстринг колонки `raw_path` в
    `tables.py::KnowledgeSource`) — переносить их значило бы плодить
    sidecar-поля без причины (CLAUDE.md §2).

    `source_id` — тот же UUID, что и строка-конверт в `public.
    knowledge_sources`, но БЕЗ `ForeignKey` туда — намеренно, не
    пропуск: FK через границу схем потребовал бы, чтобы обе строки были
    видны друг другу в момент вставки, а конверт (`register_file_for_
    ingest()`, сессия `helm_app`) и sidecar (эта таблица, сессия
    `helm_health`) пишутся в ДВУХ РАЗНЫХ транзакциях на двух разных
    соединениях — незакоммиченная строка одной транзакции не видна для
    FK-проверки в другой. Ссылочная целостность здесь проверяется
    кодом (`health_schema.py`), не базой."""

    __tablename__ = "knowledge_source_private"

    source_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column()
    original_filename: Mapped[str | None] = mapped_column(String(255))
    #: Полный диагностический текст ошибки разбора — публичный
    #: `KnowledgeIngestJob.error` получает только санитизированный код
    #: (`HEALTH_PARSE_FAILED`), не текст, который мог бы процитировать
    #: содержимое документа в сообщении исключения парсера.
    parse_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_health_knowledge_source_private_user", "knowledge_user_id"),
    )


class HealthKnowledgeChunk(HealthBase):
    """Зеркало `KnowledgeChunk` — текст и embedding чанка, доступны
    только `helm_health`. `source_id` ссылается на `HealthKnowledge
    SourcePrivate.source_id`, не напрямую на конверт: обе таблицы в
    одной схеме, владеет ими одна роль, FK в пределах своей схемы не
    открывает ничего наружу."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column()
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_source_private.source_id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(KNOWLEDGE_EMBED_DIM))
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("source_id", "ordinal", name="uq_health_knowledge_chunks_source_ordinal"),
        Index("ix_health_knowledge_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_health_knowledge_chunks_user", "knowledge_user_id"),
    )


class HealthKnowledgeRelation(HealthBase):
    """Зеркало `KnowledgeRelation` (`tables.py`) — та же §14.4-семантика,
    но для health: `to_id`/`from_id` могут прямо называть тему заметки
    («аутоиммунный гастрит»), это ровно то "health entities/topics",
    которому решение владельца при разборе P12 запрещает попадать в
    `public`. `source_id` — FK на `HealthKnowledgeSourcePrivate`, не на
    конверт (та же причина, что у `HealthKnowledgeChunk`)."""

    __tablename__ = "knowledge_relations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column()
    from_id: Mapped[str] = mapped_column(String(128), nullable=False)
    to_id: Mapped[str] = mapped_column(String(128), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("health.knowledge_source_private.source_id"))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (Index("ix_health_knowledge_relations_from", "from_id"),)


class HealthKnowledgeNote(HealthBase):
    """Зеркало `KnowledgeNote` (`tables.py`, §14.1/§14.3, ADR-019) — L2
    semantic-atomizer заметки для health: `slug`/`type` могут прямо
    называть тему заметки («аутоиммунный гастрит», «Иванов, уролог»), то
    самое "health entities/topics", которому решение владельца при
    разборе P12 запрещает попадать в `public`. Не более "особый" случай,
    чем `HealthKnowledgeRelation` — та же маршрутизация (`atomizer.py`
    домено-агностичен, ветвится только на уже существующих
    `is_health_domain()`/`health_schema_configured()`)."""

    __tablename__ = "knowledge_notes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column()
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids: Mapped[list | None] = mapped_column(JSONB)
    source_sha256: Mapped[list | None] = mapped_column(JSONB)
    sensitivity: Mapped[str] = mapped_column(String(32), default="internal", nullable=False)
    trust: Mapped[str] = mapped_column(String(32), default="extracted", nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(16), default=KnowledgeStatus.ACTIVE, nullable=False)
    supersedes: Mapped[list | None] = mapped_column(JSONB)
    contradicts: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = ts_column(default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("knowledge_user_id", "slug", name="uq_health_knowledge_notes_user_slug"),
    )


# ── semantic-v2 в health (v4.0 §14.5, «private equivalents/adapters
# under health schema») ───────────────────────────────────────────────
#
# Зеркалятся ЧЕТЫРЕ таблицы из пяти. `knowledge_semantic_runs` остаётся
# только в public и для health тоже: в ней нет ни одного поля с
# содержимым источника — счётчики окон, имя модели, её отпечаток, код
# ошибки. Прогресс разбора health-документа не раскрывает, что в нём
# написано, а зеркалить таблицу «за компанию» значило бы держать вторую
# очередь и вторую логику ревизий ради нуля чувствительных байт
# (CLAUDE.md §2). Ровно тот же довод, по которому конверт
# `public.knowledge_sources` един для всех доменов.
#
# Что здесь чувствительно и потому переехало: `canonical_label` («визит
# к гастроэнтерологу 19.08.2026»), `normalized_key`, `subtype`, `alias`
# («Безручко Д.Ю.») и `role` у ребра. Это ровно те «health entities/
# topics», которым решение владельца при разборе P12 запрещает попадать
# в public, — то же самое, за чем в health уехали `knowledge_relations`
# и `knowledge_notes`.
#
# Ссылки между схемами: `source_id` идёт на `health.knowledge_source_
# private`, как у остальных health-таблиц, а `semantic_run_id` FK не
# имеет вовсе — прогон живёт в public, а `helm_health` не имеет там
# никаких прав (см. `setup-health-role.sh`). Целостность этой одной
# ссылки — на стороне кода, как и у `source_id` сайдкара.


class HealthKnowledgeNode(HealthBase):
    """Зеркало `KnowledgeNode` (§14.5) для health."""

    __tablename__ = "knowledge_nodes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subtype: Mapped[str | None] = mapped_column(String(64))
    #: R3.1 (см. `tables.py::KnowledgeNode`) — та же пара полей и то же
    #: исправление: подвид ENTITY и тело утверждения EVENT/FACT/
    #: DECISION/CONCEPT для health.
    entity_type: Mapped[str | None] = mapped_column(String(64))
    statement_text: Mapped[str | None] = mapped_column(Text)
    canonical_label: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str | None] = mapped_column(Text)
    #: Без FK на `public.knowledge_domains`: `helm_health` не имеет прав
    #: на public вообще. Реестр доменов не содержит health-специфики —
    #: там ключ вроде `health`, а не название болезни.
    primary_domain_id: Mapped[uuid.UUID | None] = mapped_column()
    security_scope: Mapped[str] = mapped_column(
        String(32), default="internal", nullable=False)
    occurred_at_start: Mapped[datetime | None] = ts_column()
    occurred_at_end: Mapped[datetime | None] = ts_column()
    date_precision: Mapped[str | None] = mapped_column(String(8))
    valid_from: Mapped[datetime | None] = ts_column()
    valid_to: Mapped[datetime | None] = ts_column()
    status: Mapped[str] = mapped_column(
        String(16), default=SemanticNodeStatus.ACTIVE, nullable=False)
    markdown_path: Mapped[str | None] = mapped_column(Text)
    semantic_run_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = ts_column(default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        # Те же реестры, что в public. Зеркало без ограничений было бы
        # дырой ровно там, где данные чувствительнее: health-путь пишет
        # отдельная роль по отдельному соединению, и «в public проверим»
        # его не касается.
        CheckConstraint(f"kind IN ({sql_enum_values(SemanticNodeKind)})", name="kind"),
        CheckConstraint(f"status IN ({sql_enum_values(SemanticNodeStatus)})", name="status"),
        CheckConstraint(
            f"date_precision IS NULL OR date_precision IN "
            f"({sql_enum_values(SemanticDatePrecision)})",
            name="date_precision"),
        CheckConstraint(
            f"semantic_run_id IS NOT NULL OR kind IN ({_KINDS_WITHOUT_RUN_SQL})",
            name="run_required_for_atoms"),
        CheckConstraint(
            f"kind NOT IN ({_ATOM_KINDS_SQL}) OR "
            f"(statement_text IS NOT NULL AND statement_text <> '')",
            name="statement_text_required_for_atoms"),
        CheckConstraint(
            "kind <> 'entity' OR entity_type IS NOT NULL",
            name="entity_type_required_for_entity"),
        CheckConstraint(
            "kind <> 'entity' OR statement_text IS NULL",
            name="statement_text_null_for_entity"),
        Index("ix_health_knowledge_nodes_user_kind", "knowledge_user_id", "kind"),
        Index("ix_health_knowledge_nodes_resolution",
              "knowledge_user_id", "kind", "subtype", "normalized_key"),
        Index("ix_health_knowledge_nodes_run", "semantic_run_id"),
    )


class HealthKnowledgeNodeMention(HealthBase):
    """Зеркало `KnowledgeNodeMention` (§14.5) для health.

    `evidence_text_hash` считается по тексту чанка, который лежит в
    `health.knowledge_chunks`: упоминание и цитата остаются по одну
    сторону границы, и подтвердить происхождение можно, не вынося текст
    в public.
    """

    __tablename__ = "knowledge_node_mentions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_nodes.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_source_private.source_id"), nullable=False)
    window_id: Mapped[int | None] = mapped_column(Integer)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("health.knowledge_chunks.id"))
    page: Mapped[int | None] = mapped_column(Integer)
    time_start_ms: Mapped[int | None] = mapped_column(Integer)
    time_end_ms: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    evidence_text_hash: Mapped[str | None] = mapped_column(String(64))
    evidence_type: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    semantic_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(f"evidence_type IN ({sql_enum_values(SemanticEvidenceType)})",
                        name="evidence_type"),
        Index("ix_health_knowledge_node_mentions_node", "node_id"),
        Index("ix_health_knowledge_node_mentions_source", "source_id"),
        Index("ix_health_knowledge_node_mentions_run", "semantic_run_id"),
    )


class HealthKnowledgeEdge(HealthBase):
    """Зеркало `KnowledgeEdge` (§14.5) для health."""

    __tablename__ = "knowledge_edges"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    from_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_nodes.id"), nullable=False)
    to_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_nodes.id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("health.knowledge_source_private.source_id"))
    mention_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("health.knowledge_node_mentions.id"))
    evidence_node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("health.knowledge_nodes.id"))
    evidence_type: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(
        String(16), default=SemanticNodeStatus.ACTIVE, nullable=False)
    semantic_run_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(f"relation_type IN ({sql_enum_values(SemanticRelationType)})",
                        name="relation_type"),
        CheckConstraint(f"evidence_type IN ({sql_enum_values(SemanticEvidenceType)})",
                        name="evidence_type"),
        CheckConstraint(f"status IN ({sql_enum_values(SemanticNodeStatus)})", name="status"),
        CheckConstraint(
            f"semantic_run_id IS NOT NULL OR evidence_type = "
            f"'{SemanticEvidenceType.OWNER_EXPLICIT.value}'",
            name="run_required_for_derived"),
        Index("ix_health_knowledge_edges_from", "from_node_id", "relation_type"),
        Index("ix_health_knowledge_edges_to", "to_node_id", "relation_type"),
        Index("ix_health_knowledge_edges_run", "semantic_run_id"),
    )


class HealthKnowledgeEntityAlias(HealthBase):
    """Зеркало `KnowledgeEntityAlias` (§14.5) для health."""

    __tablename__ = "knowledge_entity_aliases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    entity_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_nodes.id"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("health.knowledge_source_private.source_id"))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("knowledge_user_id", "entity_node_id", "normalized_alias",
                         name="uq_health_knowledge_entity_aliases_node_alias"),
        Index("ix_health_knowledge_entity_aliases_lookup",
              "knowledge_user_id", "normalized_alias"),
    )


class HealthKnowledgeSemanticWindow(HealthBase):
    """Зеркало `KnowledgeSemanticWindow` (§14.4.1) для health.

    Зеркалится ради ОДНОГО поля: `heading_path`. «Анализы и обследования»
    → «Биохимический анализ крови» — уже медицинская информация, ровно
    того рода, ради которой заведена схема; остальное здесь — границы,
    хэши и счётчики.

    `semantic_run_id` без внешнего ключа: прогон живёт в public, куда
    `helm_health` не имеет прав. Та же причина, что у `source_id`
    сайдкара.
    """

    __tablename__ = "knowledge_semantic_windows"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    semantic_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_source_private.source_id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_window_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("health.knowledge_semantic_windows.id"))
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=SemanticWindowStatus.PENDING, nullable=False)
    nodes_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    edges_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(f"status IN ({sql_enum_values(SemanticWindowStatus)})", name="status"),
        CheckConstraint("char_end > char_start", name="span_not_empty"),
        UniqueConstraint("semantic_run_id", "ordinal",
                         name="uq_health_knowledge_semantic_windows_run_ordinal"),
        Index("ix_health_knowledge_semantic_windows_run_status", "semantic_run_id", "status"),
        Index("ix_health_knowledge_semantic_windows_source", "source_id"),
    )



class HealthKnowledgeEntityIdentity(HealthBase):
    """Зеркало `KnowledgeEntityIdentity` (R6) для health.

    Личность зеркалится целиком, потому что её подпись — «Гаврилова
    Марина Сергеевна» — сама по себе медицинская информация, когда
    известна из выписки: имя врача раскрывает, к какому специалисту
    ходил владелец. Держать её в public «ради удобства запроса» значило
    бы вынести из health ровно то, ради чего health заведена.
    """

    __tablename__ = "knowledge_entity_identities"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_label: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("knowledge_user_id", "entity_type", "normalized_key",
                         name="uq_health_knowledge_entity_identities_key"),
    )


class HealthKnowledgeEntityIdentityMember(HealthBase):
    """Зеркало `KnowledgeEntityIdentityMember` (R6) для health."""

    __tablename__ = "knowledge_entity_identity_members"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_entity_identities.id"), nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_nodes.id"), nullable=False)
    matched_on: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("knowledge_user_id", "node_id",
                         name="uq_health_knowledge_entity_identity_members_node"),
        CheckConstraint(f"matched_on IN ({sql_enum_values(EntityIdentityMatch)})",
                        name="matched_on"),
        Index("ix_health_knowledge_entity_identity_members_identity", "identity_id"),
    )


class HealthKnowledgeEntityResolutionCandidate(HealthBase):
    """Зеркало `KnowledgeEntityResolutionCandidate` (R6) для health."""

    __tablename__ = "knowledge_entity_resolution_candidates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_nodes.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("health.knowledge_entity_identities.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=EntityResolutionStatus.OPEN, nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("knowledge_user_id", "node_id", "identity_id", "reason",
                         name="uq_health_knowledge_entity_resolution_candidates_pair"),
        CheckConstraint(f"reason IN ({sql_enum_values(EntityResolutionReason)})",
                        name="reason"),
        CheckConstraint(f"status IN ({sql_enum_values(EntityResolutionStatus)})",
                        name="status"),
        Index("ix_health_knowledge_entity_resolution_candidates_open",
              "knowledge_user_id", "status"),
    )
