"""knowledge chunk embedding

Revision ID: 1584a37ac5f1
Revises: 8b2f4e7a1c93
Create Date: 2026-08-31 07:50:00.000000
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = '1584a37ac5f1'
down_revision = '8b2f4e7a1c93'
branch_labels = None
depends_on = None

#: ADR-025 — модель и размерность выбраны живым замером 31.08.2026, не
#: заранее (§33 запрещает хардкодить embedding-модель без замера). Смена
#: модели на другую размерность — новая миграция колонки, эта константа
#: не меняется на живую.
EMBED_DIM = 384


def upgrade() -> None:
    # pgvector/pgvector:pg16 — образ несёт расширение, но не создаёт его
    # автоматически. IF NOT EXISTS: повторный upgrade head (например, при
    # воспроизведении цепочки с нуля на scratch-БД) не должен падать на
    # уже существующем расширении.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Nullable: ingest откатывается на NULL, если embed-сервис недоступен
    # в момент разбора (helm_core.knowledge.embeddings.embed_texts_or_
    # none) — чанк остаётся лексически находимым без семантического слоя
    # до бэкафилла, не блокирует сам ingest.
    #
    # Без ANN-индекса (ivfflat/hnsw) в этой миграции: корпус на
    # 31.08.2026 — единицы источников, обучать ivfflat не на чем
    # (пустая/почти пустая таблица даёт бессмысленные кластеры), а точный
    # ORDER BY embedding <=> ... на таком объёме и без индекса быстрее,
    # чем время, которое ушло бы на подбор параметров индекса. Индекс —
    # отдельная additive-миграция, когда объём данных это оправдает.
    op.add_column('knowledge_chunks',
                  sa.Column('embedding', Vector(EMBED_DIM), nullable=True))


def downgrade() -> None:
    op.drop_column('knowledge_chunks', 'embedding')
    # Расширение НЕ дропается: могло использоваться до этой миграции
    # (маловероятно, но DROP EXTENSION затронул бы весь кластер, не
    # только эту колонку) — откат минимален и обратим, downgrade убирает
    # ровно то, что добавил upgrade.
