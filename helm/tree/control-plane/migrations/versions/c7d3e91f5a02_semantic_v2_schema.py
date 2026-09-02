"""semantic-v2 schema (v4.0 §14.5)

Revision ID: c7d3e91f5a02
Revises: e4a7c9f2b6d1
Create Date: 2026-09-02 18:10:00.000000

Аддитивно и обратимо — §14.5 «Migration rule»: `knowledge_notes` и
`knowledge_relations` (semantic-v1) не удаляются и этой миграцией не
трогаются вовсе. RAW, L1, чанки, память и пачки тоже не перестраиваются:
меняется семантический слой, а не то, из чего он выводится.
"""
from alembic import op
import sqlalchemy as sa

from helm_core.knowledge.rls import POLICY_NAME, apply_rls_to_table

revision = 'c7d3e91f5a02'
down_revision = 'e4a7c9f2b6d1'
branch_labels = None
depends_on = None

#: Порядок значим: `knowledge_semantic_runs` создаётся первой, на неё
#: ссылаются остальные. Список зафиксирован здесь буквально, а не взят
#: из `TENANT_SCOPED_TABLES` — по той же причине, что и в миграции
#: 4da8c9e90115: применённая миграция не должна зависеть от того, что
#: живой код добавит в общий список годы спустя.
TABLES = (
    "knowledge_semantic_runs",
    "knowledge_nodes",
    "knowledge_node_mentions",
    "knowledge_edges",
    "knowledge_entity_aliases",
)

#: Кольцевая ссылка: прогон указывает на источник, источник — на свой
#: текущий прогон. Внешний ключ поэтому вешается отдельным ALTER после
#: создания обеих таблиц (в модели тому же соответствует
#: `use_alter=True`).
SOURCE_FK = "fk_knowledge_sources_current_semantic_run_id"


def upgrade() -> None:
    op.create_table(
        "knowledge_semantic_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("semantic_version", sa.Integer(), nullable=False),
        sa.Column("extractor_model", sa.String(length=128), nullable=True),
        sa.Column("extractor_digest", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("windows_total", sa.Integer(), nullable=False),
        sa.Column("windows_processed", sa.Integer(), nullable=False),
        sa.Column("windows_failed", sa.Integer(), nullable=False),
        sa.Column("coverage_ratio", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("nodes_created", sa.Integer(), nullable=False),
        sa.Column("edges_created", sa.Integer(), nullable=False),
        sa.Column("unresolved_candidates", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_user_id"], ["knowledge_users.id"],
            name=op.f("fk_knowledge_semantic_runs_knowledge_user_id_knowledge_users")),
        sa.ForeignKeyConstraint(
            ["source_id"], ["knowledge_sources.id"],
            name=op.f("fk_knowledge_semantic_runs_source_id_knowledge_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_semantic_runs")),
    )
    op.create_index("ix_knowledge_semantic_runs_source", "knowledge_semantic_runs",
                    ["source_id", "semantic_version"])

    op.create_table(
        "knowledge_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("subtype", sa.String(length=64), nullable=True),
        sa.Column("canonical_label", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.Text(), nullable=True),
        sa.Column("primary_domain_id", sa.Uuid(), nullable=True),
        sa.Column("security_scope", sa.String(length=32), nullable=False),
        sa.Column("occurred_at_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_precision", sa.String(length=8), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("markdown_path", sa.Text(), nullable=True),
        sa.Column("semantic_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_user_id"], ["knowledge_users.id"],
            name=op.f("fk_knowledge_nodes_knowledge_user_id_knowledge_users")),
        sa.ForeignKeyConstraint(
            ["primary_domain_id"], ["knowledge_domains.id"],
            name=op.f("fk_knowledge_nodes_primary_domain_id_knowledge_domains")),
        sa.ForeignKeyConstraint(
            ["semantic_run_id"], ["knowledge_semantic_runs.id"],
            name=op.f("fk_knowledge_nodes_semantic_run_id_knowledge_semantic_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_nodes")),
    )
    op.create_index("ix_knowledge_nodes_user_kind", "knowledge_nodes",
                    ["knowledge_user_id", "kind"])
    op.create_index("ix_knowledge_nodes_resolution", "knowledge_nodes",
                    ["knowledge_user_id", "kind", "subtype", "normalized_key"])
    op.create_index("ix_knowledge_nodes_run", "knowledge_nodes", ["semantic_run_id"])

    op.create_table(
        "knowledge_node_mentions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_user_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("window_id", sa.Integer(), nullable=True),
        sa.Column("chunk_id", sa.Uuid(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("time_start_ms", sa.Integer(), nullable=True),
        sa.Column("time_end_ms", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("evidence_text_hash", sa.String(length=64), nullable=True),
        sa.Column("evidence_type", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("semantic_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["knowledge_chunks.id"],
            name=op.f("fk_knowledge_node_mentions_chunk_id_knowledge_chunks")),
        sa.ForeignKeyConstraint(
            ["knowledge_user_id"], ["knowledge_users.id"],
            name=op.f("fk_knowledge_node_mentions_knowledge_user_id_knowledge_users")),
        sa.ForeignKeyConstraint(
            ["node_id"], ["knowledge_nodes.id"],
            name=op.f("fk_knowledge_node_mentions_node_id_knowledge_nodes")),
        sa.ForeignKeyConstraint(
            ["semantic_run_id"], ["knowledge_semantic_runs.id"],
            name=op.f("fk_knowledge_node_mentions_semantic_run_id_knowledge_semantic_runs")),
        sa.ForeignKeyConstraint(
            ["source_id"], ["knowledge_sources.id"],
            name=op.f("fk_knowledge_node_mentions_source_id_knowledge_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_node_mentions")),
    )
    op.create_index("ix_knowledge_node_mentions_node", "knowledge_node_mentions", ["node_id"])
    op.create_index("ix_knowledge_node_mentions_source", "knowledge_node_mentions", ["source_id"])
    op.create_index("ix_knowledge_node_mentions_run", "knowledge_node_mentions",
                    ["semantic_run_id"])

    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_user_id", sa.Uuid(), nullable=False),
        sa.Column("from_node_id", sa.Uuid(), nullable=False),
        sa.Column("to_node_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("mention_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_node_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_type", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("semantic_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_node_id"], ["knowledge_nodes.id"],
            name=op.f("fk_knowledge_edges_evidence_node_id_knowledge_nodes")),
        sa.ForeignKeyConstraint(
            ["from_node_id"], ["knowledge_nodes.id"],
            name=op.f("fk_knowledge_edges_from_node_id_knowledge_nodes")),
        sa.ForeignKeyConstraint(
            ["knowledge_user_id"], ["knowledge_users.id"],
            name=op.f("fk_knowledge_edges_knowledge_user_id_knowledge_users")),
        sa.ForeignKeyConstraint(
            ["mention_id"], ["knowledge_node_mentions.id"],
            name=op.f("fk_knowledge_edges_mention_id_knowledge_node_mentions")),
        sa.ForeignKeyConstraint(
            ["semantic_run_id"], ["knowledge_semantic_runs.id"],
            name=op.f("fk_knowledge_edges_semantic_run_id_knowledge_semantic_runs")),
        sa.ForeignKeyConstraint(
            ["source_id"], ["knowledge_sources.id"],
            name=op.f("fk_knowledge_edges_source_id_knowledge_sources")),
        sa.ForeignKeyConstraint(
            ["to_node_id"], ["knowledge_nodes.id"],
            name=op.f("fk_knowledge_edges_to_node_id_knowledge_nodes")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_edges")),
    )
    op.create_index("ix_knowledge_edges_from", "knowledge_edges",
                    ["from_node_id", "relation_type"])
    op.create_index("ix_knowledge_edges_to", "knowledge_edges",
                    ["to_node_id", "relation_type"])
    op.create_index("ix_knowledge_edges_run", "knowledge_edges", ["semantic_run_id"])

    op.create_table(
        "knowledge_entity_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_user_id", sa.Uuid(), nullable=False),
        sa.Column("entity_node_id", sa.Uuid(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_node_id"], ["knowledge_nodes.id"],
            name=op.f("fk_knowledge_entity_aliases_entity_node_id_knowledge_nodes")),
        sa.ForeignKeyConstraint(
            ["knowledge_user_id"], ["knowledge_users.id"],
            name=op.f("fk_knowledge_entity_aliases_knowledge_user_id_knowledge_users")),
        sa.ForeignKeyConstraint(
            ["source_id"], ["knowledge_sources.id"],
            name=op.f("fk_knowledge_entity_aliases_source_id_knowledge_sources")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_entity_aliases")),
        sa.UniqueConstraint("knowledge_user_id", "entity_node_id", "normalized_alias",
                            name="uq_knowledge_entity_aliases_node_alias"),
    )
    op.create_index("ix_knowledge_entity_aliases_lookup", "knowledge_entity_aliases",
                    ["knowledge_user_id", "normalized_alias"])

    # Указатель на текущую ревизию разбора. Заполняется только кодом
    # переключения (§14.5: «only a revision whose run reached READY»),
    # backfill'а здесь нет — до R8 граф v2 не построен ни для одного
    # источника, и NULL это честное «ревизии ещё нет».
    op.add_column("knowledge_sources",
                  sa.Column("current_semantic_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(SOURCE_FK, "knowledge_sources", "knowledge_semantic_runs",
                          ["current_semantic_run_id"], ["id"])

    for table in TABLES:
        apply_rls_to_table(op.get_bind(), table)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY {POLICY_NAME} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_constraint(SOURCE_FK, "knowledge_sources", type_="foreignkey")
    op.drop_column("knowledge_sources", "current_semantic_run_id")

    for table in reversed(TABLES):
        op.drop_table(table)
