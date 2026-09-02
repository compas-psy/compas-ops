"""semantic-v2: окна обработки (v4.0 §14.4.1)

Revision ID: a5c2e08d91f4
Revises: d1b8f4e6c37a
Create Date: 2026-09-02 17:30:00.000000

Шаг R3. Аддитивно: ни одна из таблиц R2 не меняется.

Зачем таблица. §14.4.1 требует, чтобы 100% окон источника становились
терминальными, и чтобы PROCESSED-окно хранило хэш и счётчики результата
даже когда узлов ноль — иначе «в этом фрагменте нечего извлекать» и
«модель вернула неполный объект, а мы это проглотили» неразличимы. На
счётчиках прогона это не выразить: они говорят «сколько», но не
«какие именно» и не «почему».

Текст окна не хранится: он есть в L1 SOURCE, а по `char_start`/
`char_end` восстанавливается точно. Хранить его здесь значило бы завести
вторую копию содержимого источника, в том числе health.
"""
from alembic import op
import sqlalchemy as sa

from helm_core.knowledge.rls import POLICY_NAME, apply_rls_to_table

revision = 'a5c2e08d91f4'
down_revision = 'd1b8f4e6c37a'
branch_labels = None
depends_on = None

TABLE = "knowledge_semantic_windows"

#: Как и в миграциях R2 — буквально, не из живого enum: применённая
#: миграция зависит только от того, что было верно в момент её
#: написания.
WINDOW_STATUSES = "'pending', 'processed', 'no_knowledge', 'split', 'failed'"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_user_id", sa.Uuid(), nullable=False),
        sa.Column("semantic_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("parent_window_id", sa.Uuid(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("nodes_created", sa.Integer(), nullable=False),
        sa.Column("edges_created", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_user_id"], ["knowledge_users.id"],
            name=op.f("fk_knowledge_semantic_windows_knowledge_user_id_knowledge_users")),
        sa.ForeignKeyConstraint(
            ["parent_window_id"], [f"{TABLE}.id"],
            name=op.f("fk_knowledge_semantic_windows_parent_window_id_knowledge_semantic_windows")),
        sa.ForeignKeyConstraint(
            ["semantic_run_id"], ["knowledge_semantic_runs.id"],
            name=op.f("fk_knowledge_semantic_windows_semantic_run_id_knowledge_semantic_runs")),
        sa.ForeignKeyConstraint(
            ["source_id"], ["knowledge_sources.id"],
            name=op.f("fk_knowledge_semantic_windows_source_id_knowledge_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_semantic_windows")),
        sa.UniqueConstraint("semantic_run_id", "ordinal",
                            name="uq_knowledge_semantic_windows_run_ordinal"),
    )
    # Явный SQL, а не op.create_check_constraint(): тот прогоняет имя
    # через соглашение об именах и приписывает `ck_<таблица>_` второй раз
    # (разбор — в миграции d1b8f4e6c37a).
    op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_{TABLE}_status "
               f"CHECK (status IN ({WINDOW_STATUSES}))")
    op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_{TABLE}_span_not_empty "
               f"CHECK (char_end > char_start)")
    op.create_index("ix_knowledge_semantic_windows_run_status", TABLE,
                    ["semantic_run_id", "status"])
    op.create_index("ix_knowledge_semantic_windows_source", TABLE, ["source_id"])

    apply_rls_to_table(op.get_bind(), TABLE)


def downgrade() -> None:
    op.execute(f"DROP POLICY {POLICY_NAME} ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_table(TABLE)
