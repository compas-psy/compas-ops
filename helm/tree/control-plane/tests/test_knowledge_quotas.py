"""v3.8 §14.4, P8.6.4 — per-user квоты (storage/daily-ingest/queue depth)."""

from datetime import timedelta

import pytest

from helm_core.knowledge import quotas as quotas_module
from helm_core.knowledge.memory import try_remember
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.knowledge.ingest import ingest_text, register_file_for_ingest
from helm_core.knowledge.quotas import QuotaExceeded, check_and_record_ingest, check_queue_depth
from helm_core.models import KnowledgeUser, KnowledgeUserRole, KnowledgeUserUsage
from helm_core.models.base import utcnow

from conftest import SYSTEM_OWNER_ID


@pytest.fixture
def quota_user(session):
    # daily_ingest_quota_bytes намеренно намного выше storage_quota_bytes
    # — тесты на storage-квоту не должны случайно упереться в дневную.
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER, storage_quota_bytes=100,
                         daily_ingest_quota_bytes=10_000)
    session.add(user)
    session.flush()
    return user


def test_check_and_record_ingest_allows_within_quota(session, quota_user):
    check_and_record_ingest(session, knowledge_user_id=quota_user.id, size_bytes=50)
    usage = session.get(KnowledgeUserUsage, quota_user.id)
    assert usage.storage_bytes == 50
    assert usage.ingest_bytes_today == 50


def test_check_and_record_ingest_rejects_over_storage_quota(session, quota_user):
    check_and_record_ingest(session, knowledge_user_id=quota_user.id, size_bytes=90)
    with pytest.raises(QuotaExceeded) as excinfo:
        check_and_record_ingest(session, knowledge_user_id=quota_user.id, size_bytes=20)
    assert excinfo.value.kind == "storage"
    # Отклонённая попытка не должна была засчитаться.
    usage = session.get(KnowledgeUserUsage, quota_user.id)
    assert usage.storage_bytes == 90


def test_check_and_record_ingest_rejects_over_daily_ingest_quota(session):
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER, daily_ingest_quota_bytes=60)
    session.add(user)
    session.flush()

    check_and_record_ingest(session, knowledge_user_id=user.id, size_bytes=50)
    with pytest.raises(QuotaExceeded) as excinfo:
        check_and_record_ingest(session, knowledge_user_id=user.id, size_bytes=20)
    assert excinfo.value.kind == "daily_ingest"


def test_daily_ingest_counter_resets_on_a_new_day(session):
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER, daily_ingest_quota_bytes=60)
    session.add(user)
    session.flush()

    check_and_record_ingest(session, knowledge_user_id=user.id, size_bytes=50)
    usage = session.get(KnowledgeUserUsage, user.id)
    usage.updated_at = utcnow() - timedelta(days=1)
    session.flush()

    # Без сброса эта попытка превысила бы дневную квоту (50+50 > 60).
    check_and_record_ingest(session, knowledge_user_id=user.id, size_bytes=50)
    usage = session.get(KnowledgeUserUsage, user.id)
    assert usage.ingest_bytes_today == 50
    assert usage.storage_bytes == 100  # storage не сбрасывается по дням


def test_no_quota_configured_means_unlimited(session):
    # SYSTEM_OWNER: backfill не проставлял никаких лимитов.
    check_and_record_ingest(session, knowledge_user_id=SYSTEM_OWNER_ID, size_bytes=10**9)
    usage = session.get(KnowledgeUserUsage, SYSTEM_OWNER_ID)
    assert usage.storage_bytes == 10**9


def test_check_queue_depth_rejects_at_cap(session, monkeypatch, tmp_path):
    monkeypatch.setattr(quotas_module, "MAX_QUEUED_JOBS_PER_USER", 1)

    raw = tmp_path / "a.txt"
    raw.write_text("x", encoding="utf-8")
    register_file_for_ingest(session, domain="engineering", raw_path=raw, vault_root=str(tmp_path))
    session.flush()

    with pytest.raises(QuotaExceeded) as excinfo:
        check_queue_depth(session, knowledge_user_id=SYSTEM_OWNER_ID)
    assert excinfo.value.kind == "queue_depth"


def test_register_file_for_ingest_rejects_when_storage_quota_exceeded(session, quota_user, tmp_path):
    raw = tmp_path / "big.txt"
    raw.write_bytes(b"x" * 200)

    with pytest.raises(QuotaExceeded):
        register_file_for_ingest(session, domain="engineering", raw_path=raw,
                                 vault_root=str(tmp_path), knowledge_user_id=quota_user.id)

    from sqlalchemy import select
    from helm_core.models import KnowledgeSource
    assert session.scalars(select(KnowledgeSource)).all() == []


# ── счётчик сформированных записей («принцип Obsidian», 30.08.2026) ──────

def test_ingest_text_counts_the_formed_source(session, quota_user):
    ingest_text(session, domain="personal", text="Первый документ",
                knowledge_user_id=quota_user.id)
    session.flush()

    usage = session.get(KnowledgeUserUsage, quota_user.id)
    assert usage.sources_count == 1
    assert usage.memories_count == 0


def test_repeated_ingest_of_the_same_text_does_not_double_count(session, quota_user):
    """Дедуп возвращает уже существующий источник — новой записи не
    формируется, значит и считать нечего."""
    ingest_text(session, domain="personal", text="Тот же самый текст",
                knowledge_user_id=quota_user.id)
    session.flush()
    ingest_text(session, domain="personal", text="Тот же самый текст",
                knowledge_user_id=quota_user.id)
    session.flush()

    assert session.get(KnowledgeUserUsage, quota_user.id).sources_count == 1


def test_remember_counts_the_formed_memory(session, quota_user, tmp_path):
    try_remember(session, channel="telegram_knowledge", text="Запомни: код 1234",
                 knowledge_user_id=quota_user.id, vault_root=str(tmp_path))
    session.flush()

    usage = session.get(KnowledgeUserUsage, quota_user.id)
    assert usage.memories_count == 1
    assert usage.sources_count == 0


def test_duplicate_remember_does_not_double_count(session, quota_user, tmp_path):
    for _ in range(2):
        try_remember(session, channel="telegram_knowledge", text="Запомни: код 1234",
                     knowledge_user_id=quota_user.id, vault_root=str(tmp_path))
        session.flush()

    assert session.get(KnowledgeUserUsage, quota_user.id).memories_count == 1


def test_counts_do_not_leak_between_users(session, quota_user, tmp_path):
    owner_id = bind_knowledge_user(session, None)
    try_remember(session, channel="max", text="Запомни: заметка владельца",
                 knowledge_user_id=owner_id, vault_root=str(tmp_path))
    session.flush()

    assert session.get(KnowledgeUserUsage, quota_user.id) is None
    assert session.get(KnowledgeUserUsage, owner_id).memories_count == 1
