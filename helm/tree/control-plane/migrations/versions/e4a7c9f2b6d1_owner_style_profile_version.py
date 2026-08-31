"""owner style profile version bump (Z2 personal style, §14.12/P8.6.6)

Revision ID: e4a7c9f2b6d1
Revises: 9c3f1d4b7a2e
Create Date: 2026-08-31 17:45:00.000000
"""
from alembic import op


revision = 'e4a7c9f2b6d1'
down_revision = '9c3f1d4b7a2e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # helm_core/knowledge/style.py::OWNER_STYLE_VERSION = 2 — колонка
    # существовала с v3.8 backfill (default=1), но ничего её не читало.
    # rephrase.py (Z2) читает style_profile_version и зовёт
    # style_prompt_for_version() — без этого backfill'а SYSTEM_OWNER
    # остался бы на версии 1 (никогда не существовавшей в style.py),
    # style_prompt_for_version() тихо возвращала бы None, и весь
    # персональный стиль оказался бы мёртвым кодом с первого дня.
    op.execute(
        "UPDATE knowledge_users SET style_profile_version = 2 "
        "WHERE role = 'SYSTEM_OWNER'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE knowledge_users SET style_profile_version = 1 "
        "WHERE role = 'SYSTEM_OWNER'"
    )
