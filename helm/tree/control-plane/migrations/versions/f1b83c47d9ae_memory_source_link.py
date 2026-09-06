"""Связь строки памяти с источником общего жизненного цикла.

«Запомни» до 06.09.2026 писал только в `knowledge_memories`: своя
таблица, свой лексический поиск, ни чанков, ни эмбеддингов, ни узлов
графа. Разведка живого сбоя (прогон 385, «Запомни ссылки на мои каналы»
→ «Дай ссылку на мой канал B17») это подтвердила числами: запись есть,
но 0 чанков со словом b17 на весь корпус, а собственный поиск памяти дал
ранг 0.000390 при пороге 0.003.

Теперь запомненный текст идёт обычным `ingest_text()`, и эта колонка
связывает быструю запись с источником. Nullable: записи, сделанные
раньше, источника не имеют — по пустому полю их и находит догоняющий
проход.

Revision ID: f1b83c47d9ae
Revises: e4a19b73c5d2
"""

import sqlalchemy as sa
from alembic import op

revision = "f1b83c47d9ae"
down_revision = "e4a19b73c5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_memories",
                  sa.Column("source_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_knowledge_memories_source", "knowledge_memories",
                          "knowledge_sources", ["source_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_knowledge_memories_source", "knowledge_memories",
                       type_="foreignkey")
    op.drop_column("knowledge_memories", "source_id")
