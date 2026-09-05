"""R6 safety patch: reason `alias_unconfirmed`

Владелец, 05.09.2026: «Alias auto-resolution временно отключить.
Текущие `knowledge_entity_aliases` являются extractor-derived, а не
owner-confirmed... alias match → candidate.» Кандидату нужна своя
причина: записывать такое совпадение как `surname_only` значило бы
соврать в поле, по которому владелец потом будет разбирать очередь.

Только реестр причин. Ни данных, ни других таблиц миграция не трогает.

Revision ID: c9f4b21d78e5
Revises: a1e7d3c85b40
"""
from alembic import op

revision = 'c9f4b21d78e5'
down_revision = 'a1e7d3c85b40'
branch_labels = None
depends_on = None

_TABLE = 'knowledge_entity_resolution_candidates'
#: Имя уже соглашённое (`ck_<таблица>_<колонка>`), поэтому
#: оборачивается в `op.f()`: без него alembic применит
#: NAMING_CONVENTION поверх и получит `..._ck_knowledge__f270` —
#: ограничение с таким именем в базе не существует.
_NAME = 'ck_knowledge_entity_resolution_candidates_reason'


def upgrade() -> None:
    op.drop_constraint(op.f(_NAME), _TABLE, type_='check')
    op.create_check_constraint(
        op.f(_NAME), _TABLE,
        "reason IN ('surname_only', 'type_conflict', 'alias_unconfirmed')")


def downgrade() -> None:
    # Строки с новой причиной откат не переживут — и не должны: реестр
    # закрыт, а значение вне реестра в базе хуже отсутствующей строки.
    op.execute(f"DELETE FROM {_TABLE} WHERE reason = 'alias_unconfirmed'")
    op.drop_constraint(op.f(_NAME), _TABLE, type_='check')
    op.create_check_constraint(
        op.f(_NAME), _TABLE, "reason IN ('surname_only', 'type_conflict')")
