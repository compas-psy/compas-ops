"""semantic-v2: statement_text/entity_type на knowledge_nodes (R3.1)

Revision ID: b8e4f1a09c73
Revises: a5c2e08d91f4
Create Date: 2026-09-02 18:20:00.000000

R3.1 — найдено владельцем 02.09.2026 при приёмке R3: `_write_extraction()`
извлекал `atom.text` (законченное утверждение, §14.4.2), но нигде его не
сохранял — в узел уходил только `atom.title`. Одновременно `subtype`
сущности подменялся её `entity_type`, и настоящий подвид («doctor»,
«medical_specialty») терялся молча.

Аддитивно поверх применённой `a5c2e08d91f4`, та не переписывается.
Таблицы v2 пусты (проверено на живом сервере, прогон #177) — ни
бэкафилла, ни `NOT VALID` не требуется, оба CHECK ставятся сразу
валидными.

Три инварианта (заданы владельцем):
- `statement_text` обязателен и не пуст у EVENT/FACT/DECISION/CONCEPT —
  утверждение без текста нечем показать в ответе;
- `entity_type` обязателен у ENTITY — личность без классификации ничем
  не отличается от произвольной текстовой метки;
- `statement_text` запрещён у ENTITY — без этого запрета со временем
  ничто не мешало бы начать дописывать в сущность прозу источника, то
  есть в точности growing-note дефект semantic-v1 (§14.6), только через
  новое поле вместо старого.

Те же три CHECK — в `health.knowledge_nodes` (`setup-health-role.sh`,
не эта миграция: схему `health` alembic не накатывает).
"""
from alembic import op
import sqlalchemy as sa

revision = 'b8e4f1a09c73'
down_revision = 'a5c2e08d91f4'
branch_labels = None
depends_on = None

TABLE = "knowledge_nodes"
ATOM_KINDS = "'event', 'fact', 'decision', 'concept'"

CHECKS = (
    ("statement_text_required_for_atoms",
     f"kind NOT IN ({ATOM_KINDS}) OR (statement_text IS NOT NULL AND statement_text <> '')"),
    ("entity_type_required_for_entity", "kind <> 'entity' OR entity_type IS NOT NULL"),
    ("statement_text_null_for_entity", "kind <> 'entity' OR statement_text IS NULL"),
)


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("entity_type", sa.String(length=64), nullable=True))
    op.add_column(TABLE, sa.Column("statement_text", sa.Text(), nullable=True))
    # Явный SQL, а не op.create_check_constraint() — по той же причине,
    # что и в d1b8f4e6c37a: тот прогоняет имя через соглашение об именах
    # из Base.metadata и приписывает `ck_<таблица>_` второй раз.
    for name, condition in CHECKS:
        op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_{TABLE}_{name} CHECK ({condition})")


def downgrade() -> None:
    for name, _ in reversed(CHECKS):
        op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT ck_{TABLE}_{name}")
    op.drop_column(TABLE, "statement_text")
    op.drop_column(TABLE, "entity_type")
