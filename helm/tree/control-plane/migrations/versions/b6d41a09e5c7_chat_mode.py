"""Режим чата: из чего разрешено отвечать. Переживает перезапуск.

Разбор владельца 07.09.2026: `probe(paid_allowed=...)` защищал память
только на бумаге. Telegram-плагин звал его как
`paid_allowed=not in_memory_conversation`, где `in_memory_conversation`
читался из ВНУТРИПРОЦЕССНОГО словаря `_last_turn`. Перезапуск шлюза
очищал словарь — и разговор, целиком состоявший из вопросов к
собственным записям, снова получал право уйти в платную модель.
Потерянный контекст становился разрешением платить.

Строка в базе именно поэтому: свойство «этот чат отвечает только из
моей памяти» обязано жить столько же, сколько сама память. Отсутствие
строки означает `memory`, а не «неизвестно».

Revision ID: b6d41a09e5c7
Revises: a3f27e91c0d4
"""

import sqlalchemy as sa
from alembic import op

from helm_core.knowledge.rls import POLICY_NAME as RLS_POLICY, apply_rls_to_table

revision = "b6d41a09e5c7"
down_revision = "a3f27e91c0d4"
branch_labels = None
depends_on = None

TABLE = "knowledge_chat_modes"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_user_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('memory', 'paid')", name=op.f("ck_knowledge_chat_modes_mode")),
        sa.ForeignKeyConstraint(
            ["knowledge_user_id"], ["knowledge_users.id"],
            name=op.f("fk_knowledge_chat_modes_knowledge_user_id_knowledge_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chat_modes")),
        sa.UniqueConstraint("knowledge_user_id", "channel", "chat_id",
                            name="uq_knowledge_chat_modes_chat"),
    )
    # Однотабличный вызов: таблицы не существовало, когда шла общая
    # RLS-миграция (4da8c9e90115) — тот же случай, что у
    # knowledge_domains (8b2f4e7a1c93).
    apply_rls_to_table(op.get_bind(), TABLE)


def downgrade() -> None:
    op.execute(f"DROP POLICY {RLS_POLICY} ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_table(TABLE)
