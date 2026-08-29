"""RED-гейт: A-DoD п.5 и п.6, цель §30.12 «RED bypass = 0».

Это самая важная пара тестов во всём наборе. Если они проходят, а всё
остальное сломано, система всё ещё безопасна. Если наоборот — нет.
"""

import pytest
from sqlalchemy import select

from helm_core.approvals.service import AlreadyExecuted, ApprovalError
from helm_core.models import Approval, ApprovalStatus, TaskEvent

from conftest import OWNER_ID


def test_red_action_without_approval_is_blocked(session, approvals, task, monkeypatch):
    """A-DoD п.5: тестовый RED action без approval блокируется."""
    from helm_core.actions import fixtures
    monkeypatch.setattr(fixtures, "ALLOWED_PUBLIC_CHANNELS", {"tg_test"})

    with pytest.raises(ApprovalError, match="RED"):
        approvals.execute_direct("publish_public_content",
                                 {"channel": "tg_test", "body": "текст"},
                                 task_id=task.id)
    session.flush()

    blocked = session.scalars(
        select(TaskEvent).where(TaskEvent.event_type == "action.blocked_red")
    ).all()
    assert len(blocked) == 1, "блокировка RED обязана попасть в audit"


def test_approved_red_action_executes_exactly_once(session, approvals, task, monkeypatch):
    """A-DoD п.6: тот же action после approval выполняется ровно один раз."""
    from helm_core.actions import fixtures
    monkeypatch.setattr(fixtures, "ALLOWED_PUBLIC_CHANNELS", {"tg_test"})

    approval = approvals.propose("publish_public_content",
                                 {"channel": "tg_test", "body": "разрешённый текст"},
                                 task_id=task.id)
    session.flush()
    approvals.decide(approval.id, approve=True, decided_by=OWNER_ID, channel="telegram")
    session.flush()

    result = approvals.execute_approved(approval.id)
    session.flush()
    assert result["published"] is True
    assert session.get(Approval, approval.id).status == ApprovalStatus.EXECUTED

    # Второй вызов не должен дать второго эффекта.
    with pytest.raises(AlreadyExecuted):
        approvals.execute_approved(approval.id)

    executed = session.scalars(
        select(TaskEvent).where(TaskEvent.event_type == "action.executed")
    ).all()
    assert len(executed) == 1, "ровно один факт исполнения в audit"


def test_rejected_approval_never_executes(session, approvals, task, monkeypatch):
    from helm_core.actions import fixtures
    monkeypatch.setattr(fixtures, "ALLOWED_PUBLIC_CHANNELS", {"tg_test"})

    approval = approvals.propose("publish_public_content",
                                 {"channel": "tg_test", "body": "текст"}, task_id=task.id)
    session.flush()
    approvals.decide(approval.id, approve=False, decided_by=OWNER_ID, channel="panel")
    session.flush()

    with pytest.raises(ApprovalError):
        approvals.execute_approved(approval.id)


def test_green_and_yellow_execute_without_approval(session, approvals, task):
    """GREEN и YELLOW не требуют одобрения — иначе система неработоспособна."""
    green = approvals.execute_direct("notify_owner", {"text": "готово"}, task_id=task.id)
    yellow = approvals.execute_direct("kanban_snapshot", {"reason": "перед миграцией"},
                                      task_id=task.id)
    session.flush()
    assert green["queued"] is True
    assert "snapshot" in yellow

    direct = session.scalars(
        select(TaskEvent).where(TaskEvent.event_type == "action.executed_direct")
    ).all()
    assert len(direct) == 2, "YELLOW обратимо, но обязано быть записано (§8.1)"


def test_propose_auto_executes_green_and_yellow(session, approvals, task):
    """§8.1 через реальный вход Hermes — propose(), не execute_direct() напрямую.

    НАЙДЕНО на живом смоук-тесте P5/Milestone A: execute_direct() нигде не
    вызывался ни одним HTTP-роутом control-plane — только тестами напрямую.
    Через настоящий вход (propose(), которым и пользуется
    /internal/actions/propose) GREEN/YELLOW зависали в PENDING навсегда,
    хотя test_green_and_yellow_execute_without_approval выше проходил —
    он проверяет execute_direct() в обход propose().
    """
    green = approvals.propose("notify_owner", {"text": "готово"}, task_id=task.id)
    yellow = approvals.propose("kanban_snapshot", {"reason": "перед миграцией"},
                               task_id=task.id)
    session.flush()

    assert session.get(Approval, green.id).status == ApprovalStatus.EXECUTED
    assert session.get(Approval, yellow.id).status == ApprovalStatus.EXECUTED
    assert green.decided_by == "system"
    assert green.channel == "auto"


def test_brand_rules_block_publication(session, approvals, task, monkeypatch):
    """Устав §5.6 / ТЗ §10: запреты бренда сильнее метрик."""
    from helm_core.actions import fixtures
    monkeypatch.setattr(fixtures, "ALLOWED_PUBLIC_CHANNELS", {"tg_test"})

    approval = approvals.propose(
        "publish_public_content",
        {"channel": "tg_test", "body": "Не пропусти! Твой streak 7 дней"},
        task_id=task.id,
    )
    session.flush()
    approvals.decide(approval.id, approve=True, decided_by=OWNER_ID, channel="telegram")
    session.flush()

    from helm_core.actions.registry import PreconditionFailed
    with pytest.raises(PreconditionFailed) as exc:
        approvals.execute_approved(approval.id)
    assert exc.value.name == "brand_rules_checked"


def test_unknown_action_is_not_green_by_default(approvals):
    """Незарегистрированное действие не исполняется, а не считается безопасным."""
    with pytest.raises(Exception):
        approvals.execute_direct("totally_new_action", {})
