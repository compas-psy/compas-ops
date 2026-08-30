"""knowledge rls

Revision ID: 4da8c9e90115
Revises: ef1ba5467e14
Create Date: 2026-08-30 15:30:29.516033
"""
from alembic import op
import sqlalchemy as sa

from helm_core.knowledge.rls import POLICY_NAME, TENANT_SCOPED_TABLES, apply_rls

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


def upgrade() -> None:
    apply_rls(op.get_bind())


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY {POLICY_NAME} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
