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
from helm_core.models import Base

OWNER_ID = "tg:100500"
POLICY_PATH = os.environ.get("HELM_POLICY", "../config/policies/actions.yaml")
DB_URL = os.environ.get("HELM_TEST_DB", "postgresql+psycopg://helm@/helm_test?host=/tmp&port=55432")


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(DB_URL)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    """Чистая БД на каждый тест: дедупликация зависит от истории."""
    with Session(engine) as s:
        s.execute(text(
            "TRUNCATE task_events, channel_events, approvals, outbox, action_trust, "
            "artifacts, model_runs, tasks RESTART IDENTITY CASCADE"
        ))
        s.commit()
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
