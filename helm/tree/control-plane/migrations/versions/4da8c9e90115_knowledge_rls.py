"""knowledge rls

Revision ID: 4da8c9e90115
Revises: ef1ba5467e14
Create Date: 2026-08-30 15:30:29.516033
"""
from alembic import op
import sqlalchemy as sa

from helm_core.knowledge.rls import POLICY_NAME, apply_rls_to_table

revision = '4da8c9e90115'
down_revision = 'ef1ba5467e14'
branch_labels = None
depends_on = None

#: knowledge_users/channel_identities/invites/user_usage сюда намеренно
#: НЕ входят (см. TENANT_SCOPED_TABLES в helm_core/knowledge/rls.py) —
#: это сам реестр тенантов, не их контент: RLS на них — отдельная, ещё
#: не спроектированная задача Panel-ролей (P8.6.5), и включать её сейчас
#: создало бы курицу-и-яйцо для resolve_system_owner_id() (чтобы увидеть
#: свою же строку, нужно уже знать свой id).
#
#: Список ЗАФИКСИРОВАН здесь буквально, а не импортирован из
#: `TENANT_SCOPED_TABLES` — найдено 31.08.2026 при добавлении
#: `knowledge_domains` (миграция 8b2f4e7a1c93): та таблица появилась в
#: `TENANT_SCOPED_TABLES` уже ПОСЛЕ того, как эта миграция была
#: написана, и `alembic upgrade head` с нуля на пустой базе тут же упал
#: — "relation knowledge_domains does not exist", потому что в момент
#: выполнения ЭТОЙ миграции той таблицы ещё физически нет (она появится
#: только в следующей). Уже применённая в проде миграция не должна
#: зависеть от того, что живой код решит добавить в общий список
#: годы спустя — только от того, что было верно в момент её написания.
TABLES = (
    "knowledge_sources", "knowledge_chunks", "knowledge_notes", "knowledge_relations",
    "knowledge_ingest_jobs", "knowledge_pending_attachments", "knowledge_ingest_batches",
    "knowledge_batch_items", "knowledge_answer_runs", "knowledge_memories",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        apply_rls_to_table(bind, table)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY {POLICY_NAME} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
