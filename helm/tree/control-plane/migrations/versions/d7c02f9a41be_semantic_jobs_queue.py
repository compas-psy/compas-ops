"""Очередь семантического разбора: L2 перестаёт зависеть от человека.

До этой ревизии обычная загрузка файла заканчивалась на L1. `worker.py`
звал `atomize_and_store()`, замороженный с R2 и возвращающий 0, а
настоящий `publish_semantic_run()` вызывали только три ручных CLI —
backfill, пилот R5 и приёмка R10. Корпус существовал потому, что кто-то
запускал backfill руками; новый файл в память не попадал.

Миграция аддитивна: добавляется одна таблица, ничего не меняется и не
удаляется. Прежние ревизии, `current_semantic_run_id` и очередь L1 не
трогаются (§14.20).

Revision ID: d7c02f9a41be
Revises: c9f4b21d78e5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d7c02f9a41be"
down_revision = "c9f4b21d78e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_semantic_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("knowledge_user_id", sa.Uuid(),
                  sa.ForeignKey("knowledge_users.id"), nullable=True),
        sa.Column("source_id", sa.Uuid(),
                  sa.ForeignKey("knowledge_sources.id"), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("semantic_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(128), nullable=True),
        sa.Column("semantic_run_id", sa.Uuid(),
                  sa.ForeignKey("knowledge_semantic_runs.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # Ключ единственности: пользователь, источник, содержимое, версия.
        # Повторная загрузка тех же байтов той же версией не создаёт
        # второго задания; подъём версии — создаёт, и это тот самый
        # механизм, которым переносится корпус.
        sa.UniqueConstraint("knowledge_user_id", "source_id", "source_sha256",
                            "semantic_version", name="uq_knowledge_semantic_jobs_work"),
    )
    op.create_index("ix_knowledge_semantic_jobs_status", "knowledge_semantic_jobs",
                    ["status", "created_at"])

    # RLS в том же духе, что у остальных knowledge-таблиц (v3.8 фаза 1):
    # задание несёт тенанта, значит и видно оно должно быть только ему.
    op.execute("ALTER TABLE knowledge_semantic_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_semantic_jobs FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY knowledge_tenant_isolation ON knowledge_semantic_jobs
          USING (knowledge_user_id
                 = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid)
          WITH CHECK (knowledge_user_id
                 = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS knowledge_tenant_isolation ON knowledge_semantic_jobs")
    op.drop_index("ix_knowledge_semantic_jobs_status", table_name="knowledge_semantic_jobs")
    op.drop_table("knowledge_semantic_jobs")
