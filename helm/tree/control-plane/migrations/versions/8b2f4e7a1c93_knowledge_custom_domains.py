"""knowledge custom domains

Revision ID: 8b2f4e7a1c93
Revises: 4da8c9e90115
Create Date: 2026-08-31 04:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

from helm_core.knowledge.rls import POLICY_NAME, apply_rls_to_table

revision = '8b2f4e7a1c93'
down_revision = '4da8c9e90115'
branch_labels = None
depends_on = None

TABLE = "knowledge_domains"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('knowledge_user_id', sa.Uuid(), nullable=False),
        sa.Column('key', sa.String(length=32), nullable=False),
        sa.Column('use_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['knowledge_user_id'], ['knowledge_users.id'],
                               name=op.f('fk_knowledge_domains_knowledge_user_id_knowledge_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_domains')),
        sa.UniqueConstraint('knowledge_user_id', 'key', name='uq_knowledge_domains_user_key'),
    )
    # Однотабличный вызов, не apply_rls() целиком — см. docstring
    # apply_rls_to_table(): эта таблица не существовала, когда прошлая
    # RLS-миграция (4da8c9e90115) шла по TENANT_SCOPED_TABLES.
    apply_rls_to_table(op.get_bind(), TABLE)


def downgrade() -> None:
    op.execute(f"DROP POLICY {POLICY_NAME} ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_table(TABLE)
