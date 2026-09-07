"""Дата, которой датирован сам документ.

Найдено живым ответом владельцу 07.09.2026: на «какой у меня холестерин
был в последний раз» система выбрать последний анализ не могла — все
документы загружены одной пачкой, `created_at` их не различает, а даты
из текста нигде не хранились.

Nullable: дату документа определить удаётся не всегда, и «не знаю»
должно быть отличимо от «нет даты».

Revision ID: a3f27e91c0d4
Revises: f1b83c47d9ae
"""

import sqlalchemy as sa
from alembic import op

revision = "a3f27e91c0d4"
down_revision = "f1b83c47d9ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_sources", sa.Column("content_date", sa.Date(), nullable=True))
    op.create_index("ix_knowledge_sources_content_date", "knowledge_sources",
                    ["knowledge_user_id", "content_date"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_sources_content_date", table_name="knowledge_sources")
    op.drop_column("knowledge_sources", "content_date")
