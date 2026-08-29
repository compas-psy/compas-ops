"""§30.2 Control Plane — девять обязательных тестов приёмки.

Каждый тест назван по строке ТЗ, чтобы отчёт приёмки читался против
документа, а не против фантазии автора тестов.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from helm_core import outbox
from helm_core.actions.policy import Level
from helm_core.approvals.service import (
    ActionHashMismatch, AlreadyExecuted, ApprovalError, ApprovalExpired, NotAuthorized,
)
from helm_core.ingest import NotOwner
from helm_core.models import ActionTrust, Approval, ApprovalStatus, Task, utcnow
from helm_core.actions.registry import PreconditionFailed

from conftest import OWNER_ID


# 1. same message ID → one task
def test_same_message_id_creates_one_task(session, ingest):
    mid = "tg-msg-1"
    first = ingest.register(channel="telegram", external_message_id=mid,
                            owner_id=OWNER_ID, text="собери отчёт")
    session.flush()
    second = ingest.register(channel="telegram", external_message_id=mid,
                             owner_id=OWNER_ID, text="собери отчёт")
    session.flush()

    assert first.created is True
    assert second.created is False
    assert second.dedup_reason == "same_external_message_id"
    assert first.task.id == second.task.id
    assert session.scalar(select(Task).where(Task.id == first.task.id)) is not None
    assert len(session.scalars(select(Task)).all()) == 1


# 2. Telegram + MAX cross-channel duplicate → one task
def test_cross_channel_duplicate_creates_one_task(session, ingest):
    tg = ingest.register(channel="telegram", external_message_id="tg-1",
                         owner_id=OWNER_ID, text="Собери  отчёт")
    session.flush()
    mx = ingest.register(channel="max", external_message_id="max-1",
                         owner_id=OWNER_ID, text="собери отчёт")
    session.flush()

    assert mx.created is False
    assert mx.dedup_reason == "cross_channel_duplicate"
    assert tg.task.id == mx.task.id
    assert len(session.scalars(select(Task)).all()) == 1


# 3. intentional same-channel repeat → two tasks
def test_intentional_same_channel_repeat_creates_two_tasks(session, ingest):
    first = ingest.register(channel="telegram", external_message_id="tg-1",
                            owner_id=OWNER_ID, text="собери отчёт")
    session.flush()
    second = ingest.register(channel="telegram", external_message_id="tg-2",
                             owner_id=OWNER_ID, text="собери отчёт")
    session.flush()

    assert second.created is True, "повтор владельца в том же канале — новое намерение"
    assert first.task.id != second.task.id
    assert len(session.scalars(select(Task)).all()) == 2


# 4. action hash mismatch rejected
def test_action_hash_mismatch_rejected(session, approvals, task, monkeypatch):
    # Allowlist открыт намеренно: предусловия обязаны проходить, чтобы
    # единственной причиной отказа могло быть несовпадение хэша.
    from helm_core.actions import fixtures
    monkeypatch.setattr(fixtures, "ALLOWED_PUBLIC_CHANNELS", {"tg_test"})

    approval = approvals.propose(
        "publish_public_content",
        {"channel": "tg_test", "body": "исходный текст"},
        task_id=task.id,
    )
    session.flush()
    approvals.decide(approval.id, approve=True, decided_by=OWNER_ID, channel="telegram")
    session.flush()

    with pytest.raises(ActionHashMismatch):
        approvals.execute_approved(
            approval.id,
            current_payload={"channel": "tg_test", "body": "ПОДМЕНЁННЫЙ текст"},
        )

    assert session.get(Approval, approval.id).status != ApprovalStatus.EXECUTED


# 5. expired approval rejected
def test_expired_approval_rejected(session, approvals, task):
    # RED, не kanban_snapshot: с автоисполнением GREEN/YELLOW в propose()
    # (§8.1) только RED реально остаётся PENDING и ждёт decide().
    approval = approvals.propose("publish_public_content",
                                 {"channel": "tg_test", "body": "перед миграцией"},
                                 task_id=task.id)
    session.flush()
    approval.expires_at = utcnow() - timedelta(seconds=1)
    session.flush()

    with pytest.raises(ApprovalExpired):
        approvals.decide(approval.id, approve=True, decided_by=OWNER_ID, channel="panel")
    assert session.get(Approval, approval.id).status == ApprovalStatus.EXPIRED


def test_approval_expiring_after_decision_still_blocks_execution(session, approvals, task):
    """TTL проверяется и при исполнении, не только при решении (§8.4)."""
    approval = approvals.propose("publish_public_content",
                                 {"channel": "tg_test", "body": "снимок"}, task_id=task.id)
    session.flush()
    approvals.decide(approval.id, approve=True, decided_by=OWNER_ID, channel="panel")
    approval.expires_at = utcnow() - timedelta(seconds=1)
    session.flush()

    with pytest.raises(ApprovalExpired):
        approvals.execute_approved(approval.id)


# 6. changed precondition rejected
def test_changed_precondition_rejected(session, approvals, task, monkeypatch):
    from helm_core.actions import fixtures

    monkeypatch.setattr(fixtures, "ALLOWED_PUBLIC_CHANNELS", {"tg_test"})
    approval = approvals.propose("publish_public_content",
                                 {"channel": "tg_test", "body": "нормальный текст"},
                                 task_id=task.id)
    session.flush()
    approvals.decide(approval.id, approve=True, decided_by=OWNER_ID, channel="telegram")
    session.flush()

    # Между одобрением и исполнением канал вывели из allowlist.
    monkeypatch.setattr(fixtures, "ALLOWED_PUBLIC_CHANNELS", set())
    with pytest.raises(PreconditionFailed) as exc:
        approvals.execute_approved(approval.id)
    assert exc.value.name == "channel_allowlisted"
    assert session.get(Approval, approval.id).status == ApprovalStatus.FAILED


# 7. unauthorized user rejected
def test_unauthorized_user_rejected(session, approvals, ingest, task):
    # RED, не kanban_snapshot — см. комментарий в test_expired_approval_rejected.
    approval = approvals.propose("publish_public_content",
                                 {"channel": "tg_test", "body": "снимок"}, task_id=task.id)
    session.flush()

    with pytest.raises(NotAuthorized):
        approvals.decide(approval.id, approve=True, decided_by="tg:999999", channel="telegram")
    assert session.get(Approval, approval.id).status == ApprovalStatus.PENDING

    with pytest.raises(NotOwner):
        ingest.register(channel="telegram", external_message_id="tg-x",
                        owner_id="tg:999999", text="сделай что-нибудь")


# 8. outbox no duplicate
def test_outbox_no_duplicate(session):
    first = outbox.enqueue(session, channel="telegram", recipient=OWNER_ID,
                           reference="approval:abc:executed")
    session.flush()
    second = outbox.enqueue(session, channel="telegram", recipient=OWNER_ID,
                            reference="approval:abc:executed")
    session.flush()

    assert first.created is True and second.created is False
    assert first.message.id == second.message.id


def test_outbox_unique_enforced_by_database(session):
    """Дедупликация держится ограничением БД, а не только кодом."""
    outbox.enqueue(session, channel="telegram", recipient=OWNER_ID, reference="r1")
    session.flush()
    key = session.scalars(select(outbox.OutboxMessage.dedup_key)).first()
    with pytest.raises(IntegrityError):
        session.add(outbox.OutboxMessage(channel="max", recipient=OWNER_ID, dedup_key=key))
        session.flush()


# 9. trust promotion requires owner
def test_trust_promotion_requires_owner(session):
    session.add(ActionTrust(action_type="kanban_snapshot", current_level="YELLOW",
                            supervised_success=10, promoted_at=utcnow()))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(ActionTrust(action_type="kanban_snapshot", current_level="GREEN",
                            supervised_success=10, promoted_at=utcnow(),
                            promoted_by=OWNER_ID))
    session.flush()
    assert session.get(ActionTrust, "kanban_snapshot").promoted_by == OWNER_ID


def test_never_graduate_actions_cannot_be_demoted(registry):
    """§8.7: закрытый список не понижается ни при каком числе успехов."""
    for action_type in ("spend_money", "change_trust_level", "secret_rotation"):
        spec = registry.policy_for(action_type)
        assert spec.never_graduates
        with pytest.raises(Exception):
            spec.check_demotion(Level.YELLOW)
