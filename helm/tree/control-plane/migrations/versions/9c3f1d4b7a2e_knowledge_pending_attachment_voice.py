"""knowledge pending attachment voice fields

Revision ID: 9c3f1d4b7a2e
Revises: 1584a37ac5f1
Create Date: 2026-08-31 15:10:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '9c3f1d4b7a2e'
down_revision = '1584a37ac5f1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADR-021 фаза 2b: voice-«Запомни» — решение "это Remember или
    # документ" принимается только ПОСЛЕ транскрипции, асимметрично
    # текущему document-флоу (домен спрашивается сразу, до обработки).
    # server_default='document' — существующие/будущие document-pending
    # не меняют поведение, только новые voice-вложения используют его.
    op.add_column('knowledge_pending_attachments',
                  sa.Column('kind', sa.String(length=16), nullable=False,
                            server_default='document'))
    # NULL значит "ещё не транскрибировано" (для voice) или "документ, не
    # применимо" (для document) — resolve_pending_domain() различает по
    # этому полю, каким pending уже можно разрешать домен.
    op.add_column('knowledge_pending_attachments',
                  sa.Column('transcript', sa.Text(), nullable=True))
    # Куда воркер асинхронно пришлёт результат транскрипции (Remember-
    # подтверждение или меню доменов) — в отличие от document-пути, где
    # ответ уходит синхронно в момент получения файла, для voice решение
    # известно только после фоновой транскрипции.
    op.add_column('knowledge_pending_attachments',
                  sa.Column('recipient', sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column('knowledge_pending_attachments', 'recipient')
    op.drop_column('knowledge_pending_attachments', 'transcript')
    op.drop_column('knowledge_pending_attachments', 'kind')
