"""R6: canonical identity layer + resolution candidates

Три таблицы, ни одной изменённой. Распоряжение владельца 05.09.2026:
«Сделать canonical identity layer + resolution candidates... Исходные
nodes/mentions/provenance не мутировать и не удалять.» Поэтому личность
и её состав живут отдельно от `knowledge_nodes`, а откат этой миграции
возвращает граф ровно в прежнее состояние: удалять нечего, кроме самих
новых таблиц.

Зеркала в схеме `health` эта миграция НЕ создаёт и создать не может:
`helm_app` не имеет CREATE на `health` (см. докстринг
`models/health_tables.py`). Их заводит `scripts/setup-health-role.sh`,
шаг «Схема health» того же выката.

Revision ID: a1e7d3c85b40
Revises: b8e4f1a09c73
"""
# Имена внешних ключей выписаны буквально, включая хвост-хэш: их даёт
# `NAMING_CONVENTION` из `models/base.py`, усекая слишком длинные
# (`fk_..._knowle_ac64`). Написать «читаемое» имя значило бы завести
# в базе ограничение, которого нет в модели, и следующий
# autogenerate предложил бы его пересоздать.
from alembic import op
import sqlalchemy as sa

revision = 'a1e7d3c85b40'
down_revision = 'b8e4f1a09c73'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'knowledge_entity_identities',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('knowledge_user_id', sa.Uuid(), nullable=False),
        sa.Column('entity_type', sa.String(length=64), nullable=False),
        sa.Column('canonical_label', sa.Text(), nullable=False),
        sa.Column('normalized_key', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['knowledge_user_id'], ['knowledge_users.id'],
                                name='fk_knowledge_entity_identities_knowledge_user_id_knowle_ac64'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_entity_identities')),
        sa.UniqueConstraint('knowledge_user_id', 'entity_type', 'normalized_key',
                            name='uq_knowledge_entity_identities_key'),
    )
    op.create_table(
        'knowledge_entity_identity_members',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('knowledge_user_id', sa.Uuid(), nullable=False),
        sa.Column('identity_id', sa.Uuid(), nullable=False),
        sa.Column('node_id', sa.Uuid(), nullable=False),
        sa.Column('matched_on', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("matched_on IN ('normalized_label', 'alias')",
                           name=op.f('ck_knowledge_entity_identity_members_matched_on')),
        sa.ForeignKeyConstraint(['identity_id'], ['knowledge_entity_identities.id'],
                                name='fk_knowledge_entity_identity_members_identity_id_knowle_0ef9'),
        sa.ForeignKeyConstraint(['knowledge_user_id'], ['knowledge_users.id'],
                                name='fk_knowledge_entity_identity_members_knowledge_user_id__17a4'),
        sa.ForeignKeyConstraint(['node_id'], ['knowledge_nodes.id'],
                                name='fk_knowledge_entity_identity_members_node_id_knowledge_nodes'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_entity_identity_members')),
        sa.UniqueConstraint('knowledge_user_id', 'node_id',
                            name='uq_knowledge_entity_identity_members_node'),
    )
    op.create_index('ix_knowledge_entity_identity_members_identity',
                    'knowledge_entity_identity_members', ['identity_id'])
    op.create_table(
        'knowledge_entity_resolution_candidates',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('knowledge_user_id', sa.Uuid(), nullable=False),
        sa.Column('node_id', sa.Uuid(), nullable=False),
        sa.Column('identity_id', sa.Uuid(), nullable=False),
        sa.Column('reason', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reason IN ('surname_only', 'type_conflict')",
                           name=op.f('ck_knowledge_entity_resolution_candidates_reason')),
        sa.CheckConstraint("status IN ('open', 'accepted', 'rejected')",
                           name=op.f('ck_knowledge_entity_resolution_candidates_status')),
        sa.ForeignKeyConstraint(['identity_id'], ['knowledge_entity_identities.id'],
                                name='fk_knowledge_entity_resolution_candidates_identity_id_k_1b8e'),
        sa.ForeignKeyConstraint(['knowledge_user_id'], ['knowledge_users.id'],
                                name='fk_knowledge_entity_resolution_candidates_knowledge_use_724c'),
        sa.ForeignKeyConstraint(['node_id'], ['knowledge_nodes.id'],
                                name='fk_knowledge_entity_resolution_candidates_node_id_knowl_9c8f'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_entity_resolution_candidates')),
        sa.UniqueConstraint('knowledge_user_id', 'node_id', 'identity_id', 'reason',
                            name='uq_knowledge_entity_resolution_candidates_pair'),
    )
    op.create_index('ix_knowledge_entity_resolution_candidates_open',
                    'knowledge_entity_resolution_candidates',
                    ['knowledge_user_id', 'status'])

    # Тенантность — тем же способом, что у остальных Knowledge-таблиц
    # (ADR-030, миграция 4da8c9e90115): предикат в коде И политика в базе.
    # Новая таблица без политики была бы дырой ровно там, где лежат имена
    # врачей.
    for table in ('knowledge_entity_identities',
                  'knowledge_entity_identity_members',
                  'knowledge_entity_resolution_candidates'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f"""
            CREATE POLICY knowledge_tenant_isolation ON {table}
              USING (knowledge_user_id
                     = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid)
              WITH CHECK (knowledge_user_id
                     = NULLIF(current_setting('app.current_knowledge_user_id', true), '')::uuid)
        """)


def downgrade() -> None:
    op.drop_index('ix_knowledge_entity_resolution_candidates_open',
                  table_name='knowledge_entity_resolution_candidates')
    op.drop_table('knowledge_entity_resolution_candidates')
    op.drop_index('ix_knowledge_entity_identity_members_identity',
                  table_name='knowledge_entity_identity_members')
    op.drop_table('knowledge_entity_identity_members')
    op.drop_table('knowledge_entity_identities')
