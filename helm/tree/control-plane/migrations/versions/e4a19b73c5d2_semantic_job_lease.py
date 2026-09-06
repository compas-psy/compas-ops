"""Аренда задания разбора: воркер, упавший на середине, больше не теряет работу.

Дыра, найденная аудитом владельца 06.09.2026: `claim_next_semantic_job()`
брал только `PENDING`, а `RUNNING` фиксировался коммитом ДО разбора
(`worker.py`, «RUNNING виден снаружи на время разбора»). Воркер, убитый
между этим коммитом и концом разбора, оставлял задание в `RUNNING`
навсегда: ни один следующий воркер его не видел, и вернуть работу можно
было только руками через SQL или backfill.

`lease_expires_at` — срок владения. Пока он в будущем, задание считается
исполняемым и никем не подхватывается. Истёк — задание снова
претендуемо, и счётчик `attempts` ограничивает число возвратов.

Миграция аддитивна: одна колонка и один индекс. Существующие задания
получают `NULL`, что означает «аренды нет»; их подхватит первый же
воркер, как и должно быть с зависшими.

Revision ID: e4a19b73c5d2
Revises: d7c02f9a41be
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e4a19b73c5d2"
down_revision = "d7c02f9a41be"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_semantic_jobs",
                  sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    # Отбор претендуемых заданий идёт по (статус, срок аренды): и
    # PENDING, и RUNNING с истёкшей арендой.
    op.create_index("ix_knowledge_semantic_jobs_lease", "knowledge_semantic_jobs",
                    ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_semantic_jobs_lease", table_name="knowledge_semantic_jobs")
    op.drop_column("knowledge_semantic_jobs", "lease_expires_at")
