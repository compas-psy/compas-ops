"""v3.8 §9.0/§14.3, P8.6.2 — onboarding нового KNOWLEDGE_USER через
Dedicated Knowledge Bot invite flow (без HTTP/webhook — та часть в
test_knowledge_telegram_hook.py)."""

import uuid
from datetime import timedelta

import pytest

from helm_core.knowledge.onboarding import (
    consume_invite, create_invite, find_user_by_identity, reactivate_user,
    resolve_active_user_by_identity, suspend_user,
)
from helm_core.models import KnowledgeUserStatus
from helm_core.models.base import utcnow

CHANNEL = "telegram_knowledge"


def test_create_invite_creates_invited_user_and_returns_raw_token(session):
    result = create_invite(session, created_by="owner")

    assert result.user.status == KnowledgeUserStatus.INVITED
    assert result.invite.knowledge_user_id == result.user.id
    assert len(result.raw_token) > 20
    # Только хэш в БД — сырой токен нигде больше не хранится.
    assert result.invite.token_hash != result.raw_token


def test_consume_invite_binds_identity_and_activates_user(session):
    result = create_invite(session, created_by="owner")

    outcome = consume_invite(session, raw_token=result.raw_token, channel=CHANNEL,
                             external_user_id="111", external_chat_id="111")

    assert outcome.status == "success"
    assert outcome.user.id == result.user.id
    assert outcome.user.status == KnowledgeUserStatus.ACTIVE
    assert outcome.user.activated_at is not None

    resolved = resolve_active_user_by_identity(session, channel=CHANNEL, external_user_id="111")
    assert resolved is not None
    assert resolved.id == result.user.id


def test_consume_invite_is_one_use_only(session):
    result = create_invite(session, created_by="owner")
    first = consume_invite(session, raw_token=result.raw_token, channel=CHANNEL,
                           external_user_id="111", external_chat_id="111")
    second = consume_invite(session, raw_token=result.raw_token, channel=CHANNEL,
                            external_user_id="222", external_chat_id="222")

    assert first.status == "success"
    assert second.status == "used"
    # Второй (другой) Telegram-аккаунт не получил доступ через тот же токен.
    assert resolve_active_user_by_identity(session, channel=CHANNEL, external_user_id="222") is None


def test_consume_invite_rejects_expired_token(session):
    result = create_invite(session, created_by="owner", ttl=timedelta(seconds=-1))

    outcome = consume_invite(session, raw_token=result.raw_token, channel=CHANNEL,
                             external_user_id="111", external_chat_id="111")

    assert outcome.status == "expired"
    assert resolve_active_user_by_identity(session, channel=CHANNEL, external_user_id="111") is None


def test_consume_invite_rejects_unknown_token(session):
    outcome = consume_invite(session, raw_token="not-a-real-token", channel=CHANNEL,
                             external_user_id="111", external_chat_id="111")
    assert outcome.status == "invalid"


def test_consume_invite_rejects_expected_telegram_id_mismatch(session):
    result = create_invite(session, created_by="owner", expected_external_user_id="999")

    outcome = consume_invite(session, raw_token=result.raw_token, channel=CHANNEL,
                             external_user_id="111", external_chat_id="111")

    assert outcome.status == "id_mismatch"
    assert resolve_active_user_by_identity(session, channel=CHANNEL, external_user_id="111") is None


def test_consume_invite_accepts_matching_expected_telegram_id(session):
    result = create_invite(session, created_by="owner", expected_external_user_id="111")

    outcome = consume_invite(session, raw_token=result.raw_token, channel=CHANNEL,
                             external_user_id="111", external_chat_id="111")

    assert outcome.status == "success"


def test_same_telegram_identity_cannot_claim_two_active_users(session):
    invite_a = create_invite(session, created_by="owner")
    invite_b = create_invite(session, created_by="owner")

    first = consume_invite(session, raw_token=invite_a.raw_token, channel=CHANNEL,
                           external_user_id="111", external_chat_id="111")
    second = consume_invite(session, raw_token=invite_b.raw_token, channel=CHANNEL,
                            external_user_id="111", external_chat_id="111")

    assert first.status == "success"
    assert second.status == "identity_already_bound"
    # invite_b остался неиспользованным — не сожжён впустую чужой попыткой.
    assert invite_b.invite.used_at is None


def test_resolve_active_user_by_identity_returns_none_for_unknown_identity(session):
    assert resolve_active_user_by_identity(session, channel=CHANNEL, external_user_id="nobody") is None


def test_resolve_active_user_by_identity_returns_none_for_suspended_user(session):
    result = create_invite(session, created_by="owner")
    consume_invite(session, raw_token=result.raw_token, channel=CHANNEL,
                   external_user_id="111", external_chat_id="111")
    result.user.status = KnowledgeUserStatus.SUSPENDED
    session.flush()

    assert resolve_active_user_by_identity(session, channel=CHANNEL, external_user_id="111") is None
    # Но find_user_by_identity (только для UX-сообщения) всё ещё находит его.
    found = find_user_by_identity(session, channel=CHANNEL, external_user_id="111")
    assert found is not None
    assert found.status == KnowledgeUserStatus.SUSPENDED


def test_find_user_by_identity_returns_none_for_truly_unknown_identity(session):
    assert find_user_by_identity(session, channel=CHANNEL, external_user_id="nobody") is None


# ── suspend_user()/reactivate_user() ─────────────────────────────────────────

def test_suspend_user_blocks_access_and_is_idempotent(session):
    result = create_invite(session, created_by="owner")
    consume_invite(session, raw_token=result.raw_token, channel=CHANNEL,
                   external_user_id="111", external_chat_id="111")

    first = suspend_user(session, result.user.id)
    second = suspend_user(session, result.user.id)

    assert first.status == "success"
    assert second.status == "noop"
    assert result.user.status == KnowledgeUserStatus.SUSPENDED
    assert result.user.suspended_at is not None
    assert resolve_active_user_by_identity(session, channel=CHANNEL, external_user_id="111") is None


def test_reactivate_user_restores_access(session):
    result = create_invite(session, created_by="owner")
    consume_invite(session, raw_token=result.raw_token, channel=CHANNEL,
                   external_user_id="111", external_chat_id="111")
    suspend_user(session, result.user.id)

    outcome = reactivate_user(session, result.user.id)

    assert outcome.status == "success"
    assert result.user.status == KnowledgeUserStatus.ACTIVE
    assert result.user.suspended_at is None
    resolved = resolve_active_user_by_identity(session, channel=CHANNEL, external_user_id="111")
    assert resolved is not None and resolved.id == result.user.id


def test_reactivate_user_does_not_touch_deleted(session):
    result = create_invite(session, created_by="owner")
    result.user.status = KnowledgeUserStatus.DELETED
    session.flush()

    outcome = reactivate_user(session, result.user.id)

    assert outcome.status == "noop"
    assert result.user.status == KnowledgeUserStatus.DELETED


def test_suspend_user_not_found(session):
    outcome = suspend_user(session, uuid.uuid4())
    assert outcome.status == "not_found"
