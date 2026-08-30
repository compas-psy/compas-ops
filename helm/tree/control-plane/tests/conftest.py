"""Общие фикстуры. Тесты идут против настоящего PostgreSQL.

SQLite не подошёл бы: проверяются JSONB, CHECK-ограничение
`promotion_requires_owner` и поведение UNIQUE под конкурентной вставкой —
то есть ровно те свойства, которые на боевой БД и должны держать §30.2.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from helm_core.actions.fixtures import build_registry
from helm_core.approvals.service import ApprovalService
from helm_core.ingest import IngestService
from helm_core.knowledge.rls import apply_rls
from helm_core.models import Base, KnowledgeUser, KnowledgeUserRole

OWNER_ID = "tg:100500"
POLICY_PATH = os.environ.get("HELM_POLICY", "../config/policies/actions.yaml")
DB_URL = os.environ.get("HELM_TEST_DB", "postgresql+psycopg://helm@/helm_test?host=/tmp&port=55432")

#: v3.8: id фиксированный, а не uuid4() на тест, чтобы тесты, которым нужен
#: SYSTEM_OWNER явно (RLS/tenancy-тесты), могли сослаться на него без
#: дополнительного запроса к БД.
SYSTEM_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def seed_system_owner(engine) -> None:
    """Завести единственную строку `role=SYSTEM_OWNER` — предпосылка
    `resolve_system_owner_id()` (helm_core/knowledge/tenancy.py), на
    которую по умолчанию опирается каждый существующий call site
    ingest.py/batch_intake.py/chat_intake.py, ничего не передавая явно.

    Отдельная функция, не только фикстура `session` ниже: `test_api.py`/
    `test_max_channel.py` держат собственные `client`/`app` фикстуры,
    которые сами делают `Base.metadata.drop_all()+create_all()` на том же
    engine — эта строка не переживает такой сброс, её нужно заводить
    заново в каждом месте, где схема пересоздаётся.
    """
    with Session(engine) as s:
        s.add(KnowledgeUser(id=SYSTEM_OWNER_ID, role=KnowledgeUserRole.SYSTEM_OWNER))
        s.commit()


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(DB_URL)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    # v3.8: RLS-политики не часть SQLAlchemy metadata — create_all() их не
    # заводит. Без этого вызова pytest тестировал бы только ORM-схему и
    # explicit-предикаты в коде, никогда сами RLS-политики (второй слой
    # defense-in-depth, helm_core/knowledge/rls.py).
    with eng.begin() as conn:
        apply_rls(conn)
    return eng


@pytest.fixture
def session(engine):
    """Чистая БД на каждый тест: дедупликация зависит от истории."""
    with Session(engine) as s:
        s.execute(text(
            "TRUNCATE task_events, channel_events, approvals, outbox, action_trust, "
            "artifacts, model_runs, tasks, knowledge_answer_runs, knowledge_relations, "
            "knowledge_ingest_jobs, knowledge_pending_attachments, knowledge_chunks, "
            "knowledge_batch_items, knowledge_ingest_batches, "
            "knowledge_notes, knowledge_sources, knowledge_memories, "
            "knowledge_invites, knowledge_channel_identities, knowledge_user_usage, "
            "knowledge_users "
            "RESTART IDENTITY CASCADE"
        ))
        s.commit()
    seed_system_owner(engine)
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture
def registry():
    return build_registry(POLICY_PATH)


@pytest.fixture
def approvals(session, registry):
    return ApprovalService(session, registry, owner_id=OWNER_ID)


@pytest.fixture
def ingest(session):
    return IngestService(session, owner_id=OWNER_ID)


@pytest.fixture
def task(session, ingest):
    result = ingest.register(channel="telegram", external_message_id=str(uuid.uuid4()),
                             owner_id=OWNER_ID, text="исходная задача")
    session.flush()
    return result.task
