"""Таблицы Control Plane (ТЗ §7.2) + HELM Knowledge (ТЗ §14, v3.4).

Правила, действующие во всех таблицах:

- `payload_redacted` / `title_redacted` — в БД не кладётся полный сырой
  промпт без явной необходимости (§7.2). Панель никогда не показывает сырые
  промпты и содержимое клиентских заметок (бриф §7).
- денежные величины — Numeric, не float. См. actions/canonical.py.
- `task_events` защищена от UPDATE/DELETE на уровне роли БД (§7.2), а не
  только договорённостью в коде: миграция выдаёт приложению INSERT/SELECT и
  не выдаёт UPDATE/DELETE.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer,
    LargeBinary, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy.dialects.postgresql import TSVECTOR
from pgvector.sqlalchemy import Vector

from .base import (
    ApprovalStatus, Base, KnowledgeBatchItemStatus, KnowledgeBatchStatus, KnowledgeIngestStatus,
    KnowledgeMemoryStatus, KnowledgeStatus, KnowledgeUserStatus, TaskStatus, ts_column, utcnow,
    uuid_pk,
)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = ts_column(default=utcnow, onupdate=utcnow, nullable=False)
    origin_channel: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_message_id: Mapped[str | None] = mapped_column(String(128))
    origin_owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(64))
    intent: Mapped[str | None] = mapped_column(String(64))
    risk_level: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.RECEIVED, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    budget_tier: Mapped[str | None] = mapped_column(String(32))
    hermes_session_id: Mapped[str | None] = mapped_column(String(128))
    hermes_run_id: Mapped[str | None] = mapped_column(String(128))
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    title_redacted: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_tasks_status_created", "status", "created_at"),)


class TaskEvent(Base):
    """Append-only журнал (§7.2). Права на UPDATE/DELETE не выдаются."""

    __tablename__ = "task_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    timestamp: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_redacted: Mapped[dict | None] = mapped_column(JSONB)
    correlation_id: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (Index("ix_task_events_task_ts", "task_id", "timestamp"),)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Хэш ровно того payload, который одобряет владелец. Сверяется перед
    #: исполнением (§8.4): подменить параметры после одобрения нельзя.
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_encrypted_or_reference: Mapped[dict | None] = mapped_column(JSONB)
    requested_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = ts_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ApprovalStatus.PENDING, nullable=False)
    decided_at: Mapped[datetime | None] = ts_column()
    decided_by: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[str | None] = mapped_column(String(16))
    precondition_version: Mapped[str | None] = mapped_column(String(64))
    #: Ключ ровно-однократности. UNIQUE гарантирует, что повторный запуск
    #: одобренного действия не создаст второе исполнение даже при гонке.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    executed_at: Mapped[datetime | None] = ts_column()
    result_redacted: Mapped[dict | None] = mapped_column(JSONB)
    #: Короткий идентификатор для Telegram: /helm_approve <short-id> (§8.5).
    short_id: Mapped[str] = mapped_column(String(12), nullable=False)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_approvals_idempotency_key"),
        UniqueConstraint("short_id", name="uq_approvals_short_id"),
        Index("ix_approvals_status_expires", "status", "expires_at"),
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    domain: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    review_at: Mapped[datetime | None] = ts_column()


class ChannelEvent(Base):
    """Дедупликация входящих (§30.2).

    Два разных инварианта в одной таблице:
    - UNIQUE(channel, external_message_id) — повторная доставка одного и
      того же сообщения Telegram не создаёт вторую задачу;
    - UNIQUE(owner_id, normalized_hash) частичный — один и тот же текст,
      пришедший и в Telegram, и в MAX, тоже не создаёт вторую задачу.
      Намеренный повтор владельца в одном канале — создаёт, поэтому окно
      дедупликации ограничено по времени в сервисном слое, а не здесь.
    """

    __tablename__ = "channel_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))

    __table_args__ = (
        UniqueConstraint("channel", "external_message_id", name="uq_channel_events_external"),
        Index("ix_channel_events_dedup", "owner_id", "normalized_hash", "received_at"),
    )


class OutboxMessage(Base):
    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = uuid_pk()
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_reference: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    #: §30.2 «outbox no duplicate»: доставка ровно один раз обеспечивается
    #: здесь, а не ретраями с надеждой.
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (UniqueConstraint("dedup_key", name="uq_outbox_dedup_key"),)


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    timestamp: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    profile: Mapped[str | None] = mapped_column(String(64))
    alias: Mapped[str | None] = mapped_column(String(64))
    concrete_model: Mapped[str | None] = mapped_column(String(128))
    provider: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(32))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    #: Одна сохранённая строка «почему вызвана дорогая модель». Бриф §2
    #: разрешает её как единственный LLM-текст на экранах панели.
    reason_short: Mapped[str | None] = mapped_column(String(280))

    __table_args__ = (Index("ix_model_runs_ts", "timestamp"),)


class BudgetDaily(Base):
    """Снимок фактического состояния бюджета LiteLLM (§7.2).

    Источник истины по расходу — LiteLLM. Здесь проверяемый снимок для UI и
    kill-switch, а не второй счётчик: расхождение видно по source_updated_at.
    """

    __tablename__ = "budget_daily"

    id: Mapped[uuid.UUID] = uuid_pk()
    date: Mapped[datetime] = ts_column(nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"), nullable=False)
    soft_limit_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    hard_limit_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_updated_at: Mapped[datetime | None] = ts_column()

    __table_args__ = (UniqueConstraint("date", "scope", name="uq_budget_daily_date_scope"),)


class PanelSession(Base):
    __tablename__ = "panel_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = ts_column(nullable=False)
    last_seen_at: Mapped[datetime | None] = ts_column()
    revoked_at: Mapped[datetime | None] = ts_column()
    device_label: Mapped[str | None] = mapped_column(String(128))


class WebauthnCredential(Base):
    """§7.2: приватный ключ никогда не попадает на сервер — только public_key."""

    __tablename__ = "webauthn_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = ts_column()
    label: Mapped[str | None] = mapped_column(String(128))
    revoked_at: Mapped[datetime | None] = ts_column()

    __table_args__ = (UniqueConstraint("credential_id", name="uq_webauthn_credentials_credential_id"),)


class PanelEnrollmentToken(Base):
    """Только для ПЕРВОГО enrollment passkey (§10.5.7). Хранится хэш."""

    __tablename__ = "panel_enrollment_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = ts_column(nullable=False)
    used_at: Mapped[datetime | None] = ts_column()

    __table_args__ = (UniqueConstraint("token_hash", name="uq_panel_enrollment_tokens_token_hash"),)


class PanelStepUpChallenge(Base):
    """Привязка passkey-assertion к конкретной записи (§10.5.8.1).

    Без этой таблицы assertion, полученная для безобидного действия, подошла
    бы к любому другому — «одобрить рутину» превратилось бы в «слить в main».
    Живёт 60 секунд и потребляется ровно один раз.
    """

    __tablename__ = "panel_stepup_challenges"

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("panel_sessions.id"), nullable=False)
    #: Упорядоченный набор хэшей: одна церемония может подписать пакет
    #: одобрений (§10.5.8.1), но каждое исполняется отдельно.
    action_hashes: Mapped[list] = mapped_column(JSONB, nullable=False)
    approval_ids: Mapped[list | None] = mapped_column(JSONB)
    challenge: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = ts_column(nullable=False)
    used_at: Mapped[datetime | None] = ts_column()


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    sensitivity: Mapped[str] = mapped_column(String(32), default="internal", nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)


class ActionTrust(Base):
    __tablename__ = "action_trust"

    action_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    current_level: Mapped[str] = mapped_column(String(16), nullable=False)
    supervised_success: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_incident_at: Mapped[datetime | None] = ts_column()
    promoted_at: Mapped[datetime | None] = ts_column()
    #: §8.7: повышение уровня существует только по решению владельца.
    #: NULL здесь при promoted_at — признак повышения без владельца.
    promoted_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(
            "(promoted_at IS NULL) OR (promoted_by IS NOT NULL)",
            name="promotion_requires_owner",
        ),
        CheckConstraint("supervised_success >= 0", name="supervised_success_non_negative"),
    )


class Routine(Base):
    __tablename__ = "routines"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    schedule: Mapped[str] = mapped_column(String(64), nullable=False)
    profile: Mapped[str | None] = mapped_column(String(64))
    skill: Mapped[str | None] = mapped_column(String(128))
    budget_tier: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = ts_column()
    last_status: Mapped[str | None] = mapped_column(String(32))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (UniqueConstraint("name", name="uq_routines_name"),)


class MetricPoint(Base):
    """Метрики Guardian. Ретенция 90 дней (§7.2)."""

    __tablename__ = "metrics_ts"

    id: Mapped[uuid.UUID] = uuid_pk()
    timestamp: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    labels: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_metrics_ts_metric_ts", "metric", "timestamp"),)


# ── HELM Knowledge / «второй мозг» (ТЗ §14, v3.4) ──────────────────────────
#
# dense/pgvector: модель и размерность выбраны живым замером 31.08.2026
# (ADR-025) — sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2,
# 384. Смена модели на другую размерность — новая миграция колонки, не
# правка этой константы на живую: тип столбца фиксирует форму при
# создании.
KNOWLEDGE_EMBED_DIM = 384


class KnowledgeSource(Base):
    """L1 SOURCE — нормализованная версия одного исходника (§14.1, §14.4).

    RAW immutable (§14.2) живёт на диске под своим sha256; здесь только
    метаданные и путь к SOURCE.md, не сам текст — Markdown-файл и Postgres
    вместе canonical (§14.4), файл не дублируется в БД как BLOB.
    """

    __tablename__ = "knowledge_sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: v3.8 §14.2 — hard tenant key. Nullable ради additive-миграции
    #: (существующие строки бэкафилятся к SYSTEM_OWNER отдельным шагом
    #: миграции, не NOT NULL с ходу — см. V3.8-DELTA.md); новый код
    #: обязан заполнять его всегда.
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_users.id"))
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    #: По этому хэшу распознаётся повтор (§14.5: «Повторный файл с тем же
    #: SHA256 не обрабатывается заново — связывается с существующим source»).
    #: УНИКАЛЬНОСТЬ ПО ПАРЕ (knowledge_user_id, sha256), НЕ по одному
    #: sha256 — v3.8 §14.2 явно запрещает cross-user dedup ("user A
    #: content != user B content... no shared chunk-set or
    #: content-existence side channel"); глобальная уникальность отдала бы
    #: User B готовый source User A просто по совпадению байт.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    #: markitdown | docling | gigaam | manual — чем получен source_path.
    parser: Mapped[str | None] = mapped_column(String(32))
    sensitivity: Mapped[str] = mapped_column(String(32), default="internal", nullable=False)
    trust: Mapped[str] = mapped_column(String(32), default="extracted", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=KnowledgeStatus.ACTIVE, nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = ts_column(default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("knowledge_user_id", "sha256", name="uq_knowledge_sources_user_sha256"),
        Index("ix_knowledge_sources_domain_status", "domain", "status"),
        Index("ix_knowledge_sources_user", "knowledge_user_id"),
    )


class KnowledgeChunk(Base):
    """Проиндексированный кусок source — лексический слой (§14.9)."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: v3.8 §14.2 — денормализовано с родительского source (не только join)
    #: ради прямого RLS-предиката на этой таблице без обращения к
    #: knowledge_sources в каждой политике.
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_users.id"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_sources.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    time_start_ms: Mapped[int | None] = mapped_column(Integer)
    time_end_ms: Mapped[int | None] = mapped_column(Integer)
    #: §14.9: «русская конфигурация для RU». Заполняется приложением
    #: (to_tsvector('russian', text)) при вставке, не generated column —
    #: без выражения в generated column миграция проще и переносимее между
    #: минорными версиями Postgres.
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    #: ADR-025 — дополняет tsv, не заменяет (§14.12 "FTS + pgvector").
    #: Nullable: ingest откатывается на NULL, если embedding-сервис
    #: недоступен в момент разбора (embeddings.embed_texts_or_none) —
    #: чанк остаётся лексически находимым, просто без семантического слоя
    #: до бэкафилла. Без ANN-индекса пока: корпус мал (единицы источников
    #: на 31.08.2026), ivfflat/hnsw на таком объёме калибровать не на чем
    #: — точный ORDER BY embedding <=> ... достаточно быстр без индекса и
    #: добавляется отдельной additive-миграцией, когда объём это оправдает.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(KNOWLEDGE_EMBED_DIM))
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("source_id", "ordinal", name="uq_knowledge_chunks_source_ordinal"),
        Index("ix_knowledge_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_knowledge_chunks_user", "knowledge_user_id"),
    )


class KnowledgeNote(Base):
    """L2 KNOWLEDGE (§14.1, §14.3). Тело заметки — файл на диске
    (`file_path`); здесь только frontmatter-метаданные для запросов и связей.
    """

    __tablename__ = "knowledge_notes"

    id: Mapped[uuid.UUID] = uuid_pk()
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_users.id"))
    #: Стабильный id/slug из frontmatter (§14.3: «id: stable.uuid-or-slug»)
    #: — то, на что ссылаются wikilinks [[concept-id]], не суррогатный PK.
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids: Mapped[list | None] = mapped_column(JSONB)
    source_sha256: Mapped[list | None] = mapped_column(JSONB)
    sensitivity: Mapped[str] = mapped_column(String(32), default="internal", nullable=False)
    trust: Mapped[str] = mapped_column(String(32), default="extracted", nullable=False)
    #: §14.3: «только для derived/inferred» — NULL для того, что owner
    #: написал сам, число для того, что вывел HELM.
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(16), default=KnowledgeStatus.ACTIVE, nullable=False)
    supersedes: Mapped[list | None] = mapped_column(JSONB)
    contradicts: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = ts_column(default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("knowledge_user_id", "slug", name="uq_knowledge_notes_user_slug"),
    )


class KnowledgeRelation(Base):
    """§14.4: минимальные поля заданы спекой дословно."""

    __tablename__ = "knowledge_relations"

    id: Mapped[uuid.UUID] = uuid_pk()
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_users.id"))
    #: Слаг заметки или id source — не FK: узел связи может указывать на
    #: заметку, которой ещё нет как строки knowledge_notes (owner пишет
    #: wikilink на будущую заметку) — то же допущение, что у wikilinks в
    #: Obsidian (§14.3).
    from_id: Mapped[str] = mapped_column(String(128), nullable=False)
    to_id: Mapped[str] = mapped_column(String(128), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_sources.id"))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (Index("ix_knowledge_relations_from", "from_id"),)


class KnowledgeIngestJob(Base):
    """Прогресс одного ingest'а («Гиппокамп», §14.5)."""

    __tablename__ = "knowledge_ingest_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_users.id"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_sources.id"), nullable=False)
    #: telegram | max | manual — откуда пришёл файл (§14.5.1).
    channel: Mapped[str | None] = mapped_column(String(32))
    #: chat_id/адресат для уведомления о завершении разбора (P8.5.7,
    #: "3 шага": получен -> сохранён, разбор запущен -> разбор завершён).
    #: None для ingest_text()/тестовых путей — уведомлять там некого.
    #: Также None (намеренно) для job'ов, заведённых из ZIP-batch (v3.7
    #: §14.5.2 "no per-file push spam") — уведомляет только batch
    #: целиком, per-item _notify_owner_of_result() не должен сработать.
    recipient: Mapped[str | None] = mapped_column(String(128))
    #: v3.7 §14.4.0 knowledge_batch_items — заполнено, только если job
    #: заведён expand_batch() (chat_intake одиночных вложений его не
    #: трогает). Через это поле worker.py находит, какой batch-item
    #: обновить и не пора ли финализировать весь batch (см.
    #: batch_intake.py::finalize_batch_if_terminal()).
    batch_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_batch_items.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=KnowledgeIngestStatus.PENDING,
                                        nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = ts_column(default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (Index("ix_knowledge_ingest_jobs_status", "status", "created_at"),)


class KnowledgePendingAttachment(Base):
    """Файл, уже сохранённый в spool, ждущий ответа владельца с доменом
    (P8.5.7, §14.5.1 + двухшаговый диалог — решение владельца 29.08.2026).

    Owner-attachment-first: байты уходят в spool ДО какого-либо решения о
    домене (спека требует preserve-before-parse, а не только
    preserve-before-parser). Эта строка — единственный след файла между
    "получили" и "разложили в raw/<domain>/"; следующее сообщение
    владельца на ТОМ ЖЕ канале резолвит домен и завершает P8.5.7-pipeline
    (`chat_intake.py`). FIFO по `created_at` внутри одного `channel`:
    несколько неразрешённых вложений подряд — редкий, но не запрещённый
    случай, разрешается по очереди, а не последним/первым произвольно.
    """

    __tablename__ = "knowledge_pending_attachments"

    id: Mapped[uuid.UUID] = uuid_pk()
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_users.id"))
    #: telegram | max — тот же словарь, что у KnowledgeIngestJob.channel.
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    spool_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    #: Подпись/caption, отправленная вместе с файлом, если была — только
    #: для текста запроса на домен, дальше в KnowledgeSource не переносится.
    caption: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)

    __table_args__ = (Index("ix_knowledge_pending_attachments_channel_created",
                            "channel", "created_at"),)


class KnowledgeIngestBatch(Base):
    """ZIP-архив целиком (v3.7 §14.4.0/P8.5.2.1) — контейнер/оркестрация
    ПЕРЕД уже существующим одиночным child-pipeline, не замена ему.

    Строка заводится сразу при получении архива (до вопроса о домене —
    тот же принцип preserve-before-parse, что уже есть у
    `KnowledgePendingAttachment`) и живёт до самого завершения batch —
    отдельной "pending"-таблицы для диалога о домене не нужно: `status`
    сам проходит через `WAITING_DOMAIN`.
    """

    __tablename__ = "knowledge_ingest_batches"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: v3.8 §14.2 — та же анти-cross-user-dedup логика, что у
    #: KnowledgeSource.sha256: lookup по archive_sha256 фильтруется и по
    #: этому полю (batch_intake.py::stage_batch()).
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_users.id"))
    #: telegram | max — тот же словарь, что у KnowledgeIngestJob.channel.
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Адресат для итогового уведомления (§14.5.2) — тот же паттерн, что
    #: KnowledgeIngestJob.recipient.
    recipient: Mapped[str | None] = mapped_column(String(128))
    archive_filename: Mapped[str | None] = mapped_column(String(255))
    archive_mime: Mapped[str | None] = mapped_column(String(128))
    archive_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    archive_raw_path: Mapped[str] = mapped_column(Text, nullable=False)
    #: Layer-1 дедуп архива целиком (§14.6 "ZIP-specific dedup") — тот же
    #: sha256-по-байтам принцип, что уже есть у KnowledgeSource/
    #: KnowledgePendingAttachment, не новое изобретение.
    archive_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(32))
    #: Форсируется как у одиночных вложений (chat_intake.py: simpas/zapiski
    #: -> client_restricted) — отдельного понятия security_scope не вводим.
    sensitivity: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default=KnowledgeBatchStatus.RECEIVED,
                                        nullable=False)
    total_members: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    eligible_members: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ready_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quarantine_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = ts_column()
    finished_at: Mapped[datetime | None] = ts_column()
    final_notification_sent_at: Mapped[datetime | None] = ts_column()
    #: retry_failed увеличивает — новый финальный итог разрешён только для
    #: этого цикла ретрая (§14.5.2), исходный dedup_key не мешает повтору.
    completion_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_knowledge_ingest_batches_channel_created", "channel", "created_at"),
        Index("ix_knowledge_ingest_batches_user_sha256", "knowledge_user_id", "archive_sha256"),
    )


class KnowledgeBatchItem(Base):
    """Один член ZIP-архива (v3.7 §14.4.0). Путь члена внутри архива —
    ТОЛЬКО метаданные (`archive_member_path_original`), никогда не
    становится путём на диске напрямую (§14.7.6 anti zip-slip) — реальный
    child RAW адресуется по `KnowledgeSource.id`/`sha256`, как и у
    одиночных вложений."""

    __tablename__ = "knowledge_batch_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_users.id"))
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_ingest_batches.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    archive_member_path_original: Mapped[str] = mapped_column(Text, nullable=False)
    archive_member_name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    declared_compressed_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_uncompressed_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detected_mime: Mapped[str | None] = mapped_column(String(128))
    member_sha256: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_sources.id"))
    #: §14.5.2 disable_created_sources: "applies only to source records
    #: actually created by this batch, never a pre-existing duplicate".
    source_created_by_batch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=KnowledgeBatchItemStatus.QUEUED,
                                        nullable=False)
    #: Только FAILED из-за реальной ошибки парсинга/воркера — не
    #: QUARANTINE/SKIPPED_*, их retry_failed трогать не должен (§final
    #: clarifications: "retries only retryable failures").
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    chunks: Mapped[int | None] = mapped_column(Integer)
    #: Graphify не реализован (P8.5.6) — всегда NOT_APPLICABLE, финализация
    #: batch не ждёт несуществующей стадии.
    graph_status: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = ts_column(default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_knowledge_batch_items_batch_status", "batch_id", "status"),
        UniqueConstraint("batch_id", "ordinal", name="uq_knowledge_batch_items_batch_ordinal"),
    )


class KnowledgeAnswerRun(Base):
    """§14.14: поля заданы спекой дословно. Это метрика paid-AI avoidance,
    не отладочный журнал — Panel читает её напрямую («Система → Интеграции»,
    одна строка «free-answer ratio 30d»)."""

    __tablename__ = "knowledge_answer_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    knowledge_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_users.id"))
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    paid_ai_used: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    local_model: Mapped[str | None] = mapped_column(String(128))
    cloud_model: Mapped[str | None] = mapped_column(String(128))
    escalation_reason: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        Index("ix_knowledge_answer_runs_created", "created_at"),
        Index("ix_knowledge_answer_runs_user", "knowledge_user_id"),
    )


class KnowledgeUser(Base):
    """v3.8 §14.2 — тенант Knowledge, НЕ владелец HELM. Ровно одна строка
    `role=SYSTEM_OWNER` соответствует единственному сегодняшнему владельцу
    (backfill существующих данных при миграции); дополнительные строки —
    `role=KNOWLEDGE_USER`, каждая видит только собственный Second Brain."""

    __tablename__ = "knowledge_users"

    id: Mapped[uuid.UUID] = uuid_pk()
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=KnowledgeUserStatus.ACTIVE,
                                        nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    locale: Mapped[str] = mapped_column(String(8), default="ru", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)
    storage_quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    daily_ingest_quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    #: §14.1: KNOWLEDGE_USER по умолчанию НЕ может дойти до платной модели
    #: вообще — это не "предпочтение", а гейт, который вызывающий код
    #: обязан проверить до любой попытки эскалации к Hermes/OpenRouter.
    allow_paid_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    style_profile_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    activated_at: Mapped[datetime | None] = ts_column()
    suspended_at: Mapped[datetime | None] = ts_column()


class KnowledgeChannelIdentity(Base):
    """v3.8 §14.3 — Telegram `from.id` (не введённый вручную chat_id) как
    единственное доказательство identity. Уникальность на (channel,
    external_user_id): один и тот же живой Telegram-аккаунт не может
    одновременно принадлежать двум knowledge_user одновременно."""

    __tablename__ = "knowledge_channel_identities"

    id: Mapped[uuid.UUID] = uuid_pk()
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_users.id"), nullable=False)
    #: telegram_owner | telegram_knowledge | max
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_chat_id: Mapped[str | None] = mapped_column(String(64))
    verified_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revoked_at: Mapped[datetime | None] = ts_column()

    __table_args__ = (
        UniqueConstraint("channel", "external_user_id",
                        name="uq_knowledge_channel_identities_channel_user"),
        Index("ix_knowledge_channel_identities_user", "knowledge_user_id"),
    )


class KnowledgeInvite(Base):
    """v3.8 §14.3 — одноразовый deep-link токен для onboarding'а
    Dedicated Knowledge Bot. `token_hash`, не сам токен — та же
    дисциплина, что у `panel_enrollment_tokens` (владелец не хранит
    секрет, который можно предъявить, в открытом виде)."""

    __tablename__ = "knowledge_invites"

    id: Mapped[uuid.UUID] = uuid_pk()
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_external_user_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = ts_column(nullable=False)
    used_at: Mapped[datetime | None] = ts_column()
    revoked_at: Mapped[datetime | None] = ts_column()

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_knowledge_invites_token_hash"),
    )


class KnowledgeUserUsage(Base):
    """v3.8 §14.4 — квоты/backpressure, не биллинг (спека дословно)."""

    __tablename__ = "knowledge_user_usage"

    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_users.id"), primary_key=True)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    sources_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    memories_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queued_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ingest_bytes_today: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = ts_column(default=utcnow, onupdate=utcnow, nullable=False)


class KnowledgeMemory(Base):
    """v3.8 §14.10 Micro-Memory («Запомни») — НЕ document source: нет
    парсера/chunker'а, прямой FTS-юнит (эта кодовая база не строит
    embeddings ни для документов, ни для памяти — pgvector/embeddings,
    P8.5.4 остаток, не реализован, см. V3.8-DELTA.md)."""

    __tablename__ = "knowledge_memories"

    id: Mapped[uuid.UUID] = uuid_pk()
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Точный текст владельца, сохранённый как есть — §14.10 "preserve
    #: exact owner payload", никогда не переписывается генеративно.
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    display_label: Mapped[str | None] = mapped_column(String(255))
    payload_json: Mapped[dict | None] = mapped_column(JSONB)
    #: `str` через существующий `KnowledgeDomain` enum, как везде в
    #: кодовой базе — не `primary_domain_id` FK на несуществующий реестр
    #: доменов (тот же принцип, что уже применён для ZIP batch, см.
    #: V3.7-DELTA.md). Nullable — "высокая уверенность или ничего", не
    #: обязательный выбор при каждой записи (§14.10).
    domain: Mapped[str | None] = mapped_column(String(32))
    sensitivity: Mapped[str] = mapped_column(String(32), default="internal", nullable=False)
    trust: Mapped[str] = mapped_column(String(32), default="owner_asserted", nullable=False)
    valid_from: Mapped[datetime | None] = ts_column()
    expires_at: Mapped[datetime | None] = ts_column()
    status: Mapped[str] = mapped_column(String(16), default=KnowledgeMemoryStatus.ACTIVE,
                                        nullable=False)
    supersedes_memory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_memories.id"))
    #: SHA256(normalized canonical_text/payload) в пределах knowledge_user
    #: — точный повтор не создаёт второй активный item (§14.10 "Exact
    #: Micro-Memory dedup"). Не UNIQUE constraint: SUPERSEDED/EXPIRED
    #: версии того же хэша легитимно сосуществуют, уникальность
    #: "не более одного ACTIVE" проверяется в коде, не в схеме.
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_channel: Mapped[str | None] = mapped_column(String(32))
    origin_message_id: Mapped[str | None] = mapped_column(String(128))
    #: text | voice
    origin_kind: Mapped[str | None] = mapped_column(String(8))
    #: Graphify не реализован (P8.5.6) — всегда NOT_APPLICABLE, как и у
    #: KnowledgeBatchItem.graph_status.
    graph_status: Mapped[str | None] = mapped_column(String(32))
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = ts_column(default=utcnow, onupdate=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = ts_column()

    __table_args__ = (
        Index("ix_knowledge_memories_user_status", "knowledge_user_id", "status"),
        Index("ix_knowledge_memories_user_dedup", "knowledge_user_id", "dedup_hash"),
        Index("ix_knowledge_memories_tsv", "tsv", postgresql_using="gin"),
    )


class KnowledgeCustomDomain(Base):
    """Домены сверх встроенного списка (P8.5.0-11 хвост, ADR-024
    "Scalable dynamic Knowledge taxonomy" — узкий срез).

    Встроенный `KnowledgeDomain` enum (`models/base.py`) остаётся как
    есть и не удаляется: он несёт защитную семантику — `simpas/zapiski`
    принудительно получает `client_restricted` sensitivity
    (`chat_intake.py`/`batch_intake.py`), это привязка к конкретным
    Python-значениям, а не то, что можно превратить в произвольную
    строку без потери гарантии. Эта таблица — только добавка: домен,
    который владелец придумал сам, набрав имя вместо номера в меню
    (§14.5 "No hardcoded domain enum", "Bot/Panel selector:
    recent/most-used domains").

    Topics/entities/relations из того же раздела ТЗ (§14.5) сюда
    намеренно не входят — они осмысленны только вместе с Graphify
    (P8.5.6, ещё не реализован, ждёт живого сервера). Версии источников
    (D2 в §14.7) отложены отдельным решением владельца 31.08.2026: тот
    механизм по сути опирается на локальные embeddings (P8.5.4 хвост),
    которых тоже пока нет — делать его раньше значило бы либо
    переделывать, либо подменять embeddings грубой эвристикой.
    """

    __tablename__ = "knowledge_domains"

    id: Mapped[uuid.UUID] = uuid_pk()
    knowledge_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_users.id"), nullable=False)
    #: Ровно то, что дальше пишется в `domain` источника/памяти/batch'а —
    #: строка, не FK: тот же принцип, что уже применён для остальных
    #: domain-полей в кодовой базе (см. комментарий у `KnowledgeMemory.domain`).
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    #: "recent/most-used" в меню — по этому полю и `last_used_at`, не по
    #: алфавиту и не по дате создания.
    use_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = ts_column(default=utcnow, nullable=False)
    last_used_at: Mapped[datetime] = ts_column(default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("knowledge_user_id", "key", name="uq_knowledge_domains_user_key"),
    )
