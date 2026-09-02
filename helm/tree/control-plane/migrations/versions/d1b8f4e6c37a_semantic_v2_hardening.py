"""semantic-v2 hardening: гейт текущей ревизии, закрытые реестры, цикл ревизии

Revision ID: d1b8f4e6c37a
Revises: c7d3e91f5a02
Create Date: 2026-09-02 19:40:00.000000

Аддитивно поверх `c7d3e91f5a02`, которая уже применена и не
переписывается. Три группы изменений:

1. Триггеры §14.5 — `current_semantic_run_id` может указывать только на
   ревизию READY того же источника и того же владельца, и такая
   ревизия не может испортиться, пока остаётся текущей.
2. CHECK на закрытые реестры. Python-enum не мешает ни `psql`, ни
   миграции, ни коду в обход модели положить в колонку что угодно;
   реестр §14.9 назван закрытым — значит закрыт и в базе.
3. Жизненный цикл `semantic_run_id`: утверждение и упоминание обязаны
   знать свой проход, личность (ENTITY, а также DOCUMENT_REF/
   MEMORY_REF — решение 02.09.2026, разбор в `models/base.py`) — нет.

Значения реестров выписаны здесь БУКВАЛЬНО, а не собраны из enum, — по
той же причине, что список таблиц в миграции 4da8c9e90115: применённая
миграция обязана зависеть только от того, что было верно в момент её
написания. Расхождение между базой и живым enum ловит тест
`test_knowledge_semantic_v2_registry.py`, а не эта миграция.

Таблицы пусты (проверено на живом сервере, прогон #169), поэтому
`SET NOT NULL` и добавление CHECK не требуют ни бэкафилла, ни
`NOT VALID`.
"""
from alembic import op

from helm_core.knowledge.semantic_guards import apply_semantic_guards, drop_semantic_guards

revision = 'd1b8f4e6c37a'
down_revision = 'c7d3e91f5a02'
branch_labels = None
depends_on = None

NODE_KINDS = "'entity', 'event', 'fact', 'decision', 'concept', 'document_ref', 'memory_ref'"
NODE_STATUSES = "'active', 'disabled', 'superseded', 'quarantine', 'deleted'"
DATE_PRECISIONS = "'day', 'month', 'year', 'unknown'"
EVIDENCE_TYPES = "'owner_explicit', 'extracted', 'inferred'"
RUN_STATUSES = "'pending', 'running', 'ready', 'degraded', 'failed'"
RELATION_TYPES = (
    "'involves', 'has_role', 'about', 'located_at', 'part_of', 'created_by', "
    "'owned_by', 'resulted_in', 'reason_for', 'supports', 'contradicts', "
    "'supersedes', 'derived_from', 'refers_to', 'related_to'"
)
#: Виды-личности: у них ревизия не обязательна.
KINDS_WITHOUT_RUN = "'document_ref', 'entity', 'memory_ref'"

CHECKS = (
    ("knowledge_nodes", "ck_knowledge_nodes_kind", f"kind IN ({NODE_KINDS})"),
    ("knowledge_nodes", "ck_knowledge_nodes_status", f"status IN ({NODE_STATUSES})"),
    ("knowledge_nodes", "ck_knowledge_nodes_date_precision",
     f"date_precision IS NULL OR date_precision IN ({DATE_PRECISIONS})"),
    ("knowledge_nodes", "ck_knowledge_nodes_run_required_for_atoms",
     f"semantic_run_id IS NOT NULL OR kind IN ({KINDS_WITHOUT_RUN})"),
    ("knowledge_node_mentions", "ck_knowledge_node_mentions_evidence_type",
     f"evidence_type IN ({EVIDENCE_TYPES})"),
    ("knowledge_edges", "ck_knowledge_edges_relation_type",
     f"relation_type IN ({RELATION_TYPES})"),
    ("knowledge_edges", "ck_knowledge_edges_evidence_type",
     f"evidence_type IN ({EVIDENCE_TYPES})"),
    ("knowledge_edges", "ck_knowledge_edges_status", f"status IN ({NODE_STATUSES})"),
    ("knowledge_edges", "ck_knowledge_edges_run_required_for_derived",
     "semantic_run_id IS NOT NULL OR evidence_type = 'owner_explicit'"),
    ("knowledge_semantic_runs", "ck_knowledge_semantic_runs_status",
     f"status IN ({RUN_STATUSES})"),
)


def upgrade() -> None:
    # Явный SQL, а не `op.create_check_constraint()`: тот прогоняет имя
    # через соглашение об именах из `Base.metadata` и приписывает
    # `ck_<таблица>_` ВТОРОЙ раз, а слишком длинное имя ещё и обрезает
    # хэшем. Схема после миграции тогда расходится с той, что
    # `create_all()` строит в тестах, — расхождение, которого
    # `compare_metadata` не видит (CHECK он не сравнивает).
    for table, name, condition in CHECKS:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition})")

    # Упоминание — всегда продукт прохода, исключений нет: полноценный
    # NOT NULL, а не CHECK с оговорками.
    op.alter_column("knowledge_node_mentions", "semantic_run_id", nullable=False)

    apply_semantic_guards(op.get_bind())


def downgrade() -> None:
    drop_semantic_guards(op.get_bind())
    op.alter_column("knowledge_node_mentions", "semantic_run_id", nullable=True)
    for table, name, _ in reversed(CHECKS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
