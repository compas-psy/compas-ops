"""Сессия для схемы `health` — отдельная роль БД, отдельное соединение
(ADR-005/P12, ТЗ §4.5/§6.5).

`helm_app` (обычная сессия, `deps.get_session`) не имеет НИКАКИХ прав
на схему `health` — не «не должен читать», а физически не может:
`REVOKE ALL ON SCHEMA public FROM helm_health` в `compose/init/01-
databases.sql` работает в обе стороны по духу решения, а `scripts/
setup-health-role.sh` не выдаёт `helm_app` ничего на `health` в принципе.
Поэтому health-путь не может переиспользовать сессию, которую держит
вызывающий код — здесь всегда НОВОЕ соединение, с других credentials.

Пусто `settings.health_database_url` — значит `scripts/setup-health-
role.sh` ещё не прогнан на этом сервере: `health_session_or_none()`
возвращает `None`, вызывающий код (`ingest.py`/`worker.py`/`probe.py`/
`documents.py`) обязан деградировать на прежнее поведение (health в
`public`, отфильтрован в probe() по `domain`), не падать.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from ..models import HealthKnowledgeChunk, HealthKnowledgeRelation, HealthKnowledgeSourcePrivate


@lru_cache
def _health_engine_or_none() -> Engine | None:
    url = get_settings().health_database_url
    if not url:
        return None
    return create_engine(url, pool_pre_ping=True)


def health_schema_configured() -> bool:
    return _health_engine_or_none() is not None


def set_current_knowledge_user_health(session: Session, knowledge_user_id: uuid.UUID) -> None:
    """Тот же `SET LOCAL app.current_knowledge_user_id`, что и
    `tenancy.set_current_knowledge_user()`, но на health-соединении —
    RLS-предикат в `scripts/setup-health-role.sh` читает тот же GUC."""
    session.execute(
        text("SELECT set_config('app.current_knowledge_user_id', :id, true)"),
        {"id": str(knowledge_user_id)},
    )


def is_health_domain(domain: str) -> bool:
    from ..models import KnowledgeDomain
    return domain == KnowledgeDomain.HEALTH


@contextmanager
def health_session(knowledge_user_id: uuid.UUID) -> Iterator[Session]:
    """Короткоживущая сессия на health-соединении, уже привязанная к
    тенанту. Коммитит на успешном выходе, откатывает на исключении —
    вызывающий код не обязан помнить об этом сам, в отличие от обычной
    сессии (которую всегда даёт и коммитит вызывающий код FastAPI/
    воркера) здесь соединение отдельное и живёт ровно на время `with`.
    """
    engine = _health_engine_or_none()
    if engine is None:
        raise RuntimeError(
            "health_session() вызван без health_database_url — "
            "проверьте health_schema_configured() перед вызовом")
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        set_current_knowledge_user_health(session, knowledge_user_id)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def write_original_filename(*, source_id: uuid.UUID, knowledge_user_id: uuid.UUID,
                            original_filename: str | None) -> None:
    """Единственное реально чувствительное поле health-source (см.
    докстринг `HealthKnowledgeSourcePrivate`). Вызывается синхронно из
    `register_file_for_ingest()`/`ingest_text()` сразу после того, как
    конверт в `public.knowledge_sources` получил `id` — на ОТДЕЛЬНОМ
    соединении, коммитится независимо от транзакции конверта."""
    with health_session(knowledge_user_id) as session:
        session.add(HealthKnowledgeSourcePrivate(
            source_id=source_id, knowledge_user_id=knowledge_user_id,
            original_filename=original_filename,
        ))


def read_original_filename(*, source_id: uuid.UUID, knowledge_user_id: uuid.UUID) -> str | None:
    with health_session(knowledge_user_id) as session:
        return session.scalar(
            select(HealthKnowledgeSourcePrivate.original_filename)
            .where(HealthKnowledgeSourcePrivate.source_id == source_id))


def record_parse_error(*, source_id: uuid.UUID, knowledge_user_id: uuid.UUID,
                       message: str) -> None:
    """Полный диагностический текст — только сюда. `KnowledgeIngestJob.
    error` (public) получает исключительно `"HEALTH_PARSE_FAILED"`."""
    with health_session(knowledge_user_id) as session:
        private = session.get(HealthKnowledgeSourcePrivate, source_id)
        if private is not None:
            private.parse_error = message


def write_chunks(*, source_id: uuid.UUID, knowledge_user_id: uuid.UUID,
                 chunks: list[str], embeddings: list[list[float] | None]) -> int:
    with health_session(knowledge_user_id) as session:
        for ordinal, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            session.add(HealthKnowledgeChunk(
                knowledge_user_id=knowledge_user_id, source_id=source_id, ordinal=ordinal,
                text=chunk_text, tsv=func.to_tsvector("russian", chunk_text),
                embedding=embedding,
            ))
    return len(chunks)


def write_relations(*, source_id: uuid.UUID, knowledge_user_id: uuid.UUID, from_id: str,
                    relations: list[tuple[str, str, str]]) -> int:
    """`relations` — `(to_id, relation_type, evidence_type)`, уже
    извлечённые `relations.py::extract_relations()`. Принимает кортежи,
    не `ExtractedRelation`, специально: `relations.py` вызывает эту
    функцию (маршрутизация по домену), а `health_schema.py` не должен
    импортировать `relations.py` в ответ — цикл импорта.

    Идемпотентно на повторный ingest, тот же приём, что и `relations.py::
    store_relations()`: старые relations с тем же `(knowledge_user_id,
    from_id, source_id)` удаляются перед вставкой."""
    with health_session(knowledge_user_id) as session:
        session.query(HealthKnowledgeRelation).filter(
            HealthKnowledgeRelation.knowledge_user_id == knowledge_user_id,
            HealthKnowledgeRelation.from_id == from_id,
            HealthKnowledgeRelation.source_id == source_id,
        ).delete(synchronize_session=False)
        for to_id, relation_type, evidence_type in relations:
            session.add(HealthKnowledgeRelation(
                knowledge_user_id=knowledge_user_id, from_id=from_id, to_id=to_id,
                relation_type=relation_type, evidence_type=evidence_type, source_id=source_id,
            ))
    return len(relations)
