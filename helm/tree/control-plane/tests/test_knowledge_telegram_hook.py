"""v3.8 §9.0/§14.3, P8.6.2 — Dedicated Knowledge Bot вебхук
(`/hooks/knowledge-telegram`): onboarding через deep-link, tenant-scoped
Remember/recall, изоляция от owner chief bot/Hermes.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from helm_core.app import create_app
from helm_core.channels.telegram_knowledge import WEBHOOK_SECRET_HEADER
from helm_core.config import Settings
from helm_core.knowledge.onboarding import create_invite
from helm_core.knowledge.rls import apply_rls
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import Base, KnowledgeMemory, KnowledgeUser, KnowledgeUserStatus, OutboxMessage

from conftest import DB_URL, OWNER_ID, POLICY_PATH, SYSTEM_OWNER_ID, seed_system_owner

SERVICE_SECRET = "test-service-secret"
WEBHOOK_SECRET = "test-knowledge-telegram-webhook-secret"


@pytest.fixture
def app(engine, tmp_path, monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_rls(conn)
    seed_system_owner(engine)
    settings = Settings(database_url=DB_URL, policy_path=POLICY_PATH, owner_id=OWNER_ID)
    application = create_app(settings, service_secret=SERVICE_SECRET)
    application.state.knowledge_telegram_webhook_secret = WEBHOOK_SECRET

    import helm_core.api.hooks_knowledge_telegram as hook_module
    vault_root = str(tmp_path / "vault")
    real_try_remember = hook_module.try_remember
    monkeypatch.setattr(hook_module, "try_remember",
                        lambda *a, **kw: real_try_remember(*a, vault_root=vault_root, **kw))
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def post_hook(client, update: dict, *, secret: str | None = WEBHOOK_SECRET):
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers[WEBHOOK_SECRET_HEADER] = secret
    return client.post("/hooks/knowledge-telegram", content=json.dumps(update).encode(),
                       headers=headers)


def _private_message(text: str | None = None, *, user_id: int = 555, chat_id: int = 555,
                     message_id: int = 1, chat_type: str = "private", attachment: bool = False) -> dict:
    message: dict = {
        "message_id": message_id,
        "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
        "chat": {"id": chat_id, "type": chat_type},
        "date": 1,
    }
    if text is not None:
        message["text"] = text
    if attachment:
        message["document"] = {"file_id": "abc", "file_name": "note.pdf"}
    return {"update_id": 1, "message": message}


def _invite(session, **kwargs):
    result = create_invite(session, created_by=OWNER_ID, **kwargs)
    session.commit()
    return result


# ── webhook secret ───────────────────────────────────────────────────────────

def test_webhook_rejects_wrong_secret(client):
    response = post_hook(client, _private_message("hello"), secret="wrong")
    assert response.status_code == 403


def test_webhook_rejects_missing_secret(client):
    response = post_hook(client, _private_message("hello"), secret=None)
    assert response.status_code == 403


def test_webhook_fails_closed_when_no_secret_configured(app, client):
    app.state.knowledge_telegram_webhook_secret = ""
    response = post_hook(client, _private_message("hello"), secret="")
    assert response.status_code == 403


# ── /start onboarding ────────────────────────────────────────────────────────

def test_start_with_valid_invite_binds_identity_and_activates(app, client):
    with app.state.session_factory() as session:
        invite = _invite(session)
        user_id = invite.user.id
        raw_token = invite.raw_token

    response = post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=111))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "invite_success"
    with app.state.session_factory() as session:
        user = session.get(KnowledgeUser, user_id)
        assert user.status == KnowledgeUserStatus.ACTIVE
        message = session.scalars(select(OutboxMessage)).first()
        assert message is not None


def test_start_with_expired_invite_is_rejected(app, client):
    from datetime import timedelta
    with app.state.session_factory() as session:
        invite = _invite(session, ttl=timedelta(seconds=-1))
        raw_token = invite.raw_token

    response = post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=111))

    assert response.status_code == 200
    assert response.json()["status"] == "invite_expired"


def test_start_with_reused_invite_is_rejected(app, client):
    with app.state.session_factory() as session:
        invite = _invite(session)
        raw_token = invite.raw_token

    first = post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=111))
    second = post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=222))

    assert first.json()["status"] == "invite_success"
    assert second.json()["status"] == "invite_used"


def test_start_with_mismatched_expected_id_is_rejected(app, client):
    with app.state.session_factory() as session:
        invite = _invite(session, expected_external_user_id="999")
        raw_token = invite.raw_token

    response = post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=111))

    assert response.json()["status"] == "invite_id_mismatch"


def test_start_without_token_gets_no_access(client):
    response = post_hook(client, _private_message("/start"))
    assert response.json()["status"] == "no_invite"


def test_same_telegram_identity_cannot_claim_two_active_users(app, client):
    with app.state.session_factory() as session:
        invite_a = _invite(session)
        invite_b = _invite(session)

    first = post_hook(client, _private_message(f"/start kb_{invite_a.raw_token}", user_id=111))
    second = post_hook(client, _private_message(f"/start kb_{invite_b.raw_token}", user_id=111))

    assert first.json()["status"] == "invite_success"
    assert second.json()["status"] == "invite_identity_already_bound"


# ── ordinary messages: access control ───────────────────────────────────────

def test_unknown_user_gets_no_access(client):
    response = post_hook(client, _private_message("Запомни что угодно", user_id=42))
    assert response.status_code == 200
    assert response.json()["status"] == "unknown_user"


def test_suspended_user_is_rejected(app, client):
    with app.state.session_factory() as session:
        invite = _invite(session)
        raw_token = invite.raw_token
        user_id = invite.user.id
    post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=111))
    with app.state.session_factory() as session:
        user = session.get(KnowledgeUser, user_id)
        user.status = KnowledgeUserStatus.SUSPENDED
        session.commit()

    response = post_hook(client, _private_message("Запомни что угодно", user_id=111))

    assert response.json()["status"] == "suspended"


def test_group_chat_message_is_ignored(client):
    response = post_hook(client, _private_message("Запомни что угодно", user_id=111,
                                                   chat_type="group"))
    assert response.json()["status"] == "ignored"


def test_attachment_gets_not_supported_notice_not_silence(app, client):
    with app.state.session_factory() as session:
        invite = _invite(session)
        raw_token = invite.raw_token
    post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=111))

    response = post_hook(client, _private_message(user_id=111, attachment=True))

    assert response.json()["status"] == "attachment_not_supported"


# ── Remember/recall: tenant-scoped, isolated from SYSTEM_OWNER ──────────────

def test_remember_stores_memory_scoped_to_secondary_user(app, client):
    with app.state.session_factory() as session:
        invite = _invite(session)
        raw_token = invite.raw_token
        user_id = invite.user.id
    post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=111))

    response = post_hook(client, _private_message("Запомни номер машины курьера: А123ВС77",
                                                   user_id=111, message_id=2))

    assert response.json()["status"] == "remember_stored"
    with app.state.session_factory() as session:
        bind_knowledge_user(session, user_id)
        memory = session.scalars(select(KnowledgeMemory)).one()
        assert memory.knowledge_user_id == user_id
        assert "А123ВС77" in memory.canonical_text

        # SYSTEM_OWNER не видит память secondary-пользователя.
        bind_knowledge_user(session, SYSTEM_OWNER_ID)
        assert session.scalars(select(KnowledgeMemory)).all() == []


def test_secondary_user_probe_never_reaches_needs_reasoning_paid_ai(app, client):
    with app.state.session_factory() as session:
        invite = _invite(session)
        raw_token = invite.raw_token
    post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=111))

    response = post_hook(client, _private_message("какое решение приняли по проекту",
                                                   user_id=111, message_id=2))

    assert response.json()["status"] == "needs_reasoning_no_paid_ai"


def test_secondary_user_probe_does_not_see_system_owner_document_corpus(app, client):
    """probe() ещё не ищет по KnowledgeMemory вообще (recall-интеграция
    Micro-Memory в probe() — явный, задокументированный пробел этого
    захода, V3.8-DELTA.md) — изоляция здесь проверяется на document-
    корпусе (KnowledgeChunk/KnowledgeSource), который probe() уже ищет
    сегодня для ОБОИХ ролей."""
    from helm_core.knowledge.ingest import ingest_text

    with app.state.session_factory() as session:
        ingest_text(session, domain="engineering",
                   text="Решение по секретному проекту: используем Postgres.")
        session.commit()

        invite = _invite(session)
        raw_token = invite.raw_token
    post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=111))

    response = post_hook(client, _private_message("какое решение приняли по секретному проекту",
                                                   user_id=111, message_id=2))

    assert response.json()["status"] == "needs_reasoning_no_paid_ai"


# ── §14.16: управление памятью через Dedicated Knowledge Bot ─────────────

def _onboard(app, client, user_id: int):
    with app.state.session_factory() as session:
        invite = _invite(session)
        raw_token, knowledge_user_id = invite.raw_token, invite.user.id
    post_hook(client, _private_message(f"/start kb_{raw_token}", user_id=user_id))
    return knowledge_user_id


def test_secondary_user_can_forget_and_restore_own_memory(app, client):
    """Замыкает жизненный цикл: до §14.16 состояние «забыто» было
    достижимо только правкой базы руками."""
    user_id = _onboard(app, client, 777)
    post_hook(client, _private_message("Запомни: код домофона 4512",
                                       user_id=777, message_id=2))

    forgotten = post_hook(client, _private_message("Забудь про код домофона",
                                                   user_id=777, message_id=3))

    assert forgotten.json()["status"] == "admin_forgotten"
    with app.state.session_factory() as session:
        bind_knowledge_user(session, user_id)
        assert session.scalars(select(KnowledgeMemory)).one().status == "DISABLED"

    restored = post_hook(client, _private_message("Верни в память код домофона",
                                                  user_id=777, message_id=4))

    assert restored.json()["status"] == "admin_restored"
    with app.state.session_factory() as session:
        bind_knowledge_user(session, user_id)
        assert session.scalars(select(KnowledgeMemory)).one().status == "ACTIVE"


def test_forget_is_not_understood_as_a_search_request(app, client):
    """«Забудь про код домофона» иначе ушло бы в обычный поиск и было бы
    понято как просьба этот код НАЙТИ — ровно наоборот сказанному."""
    _onboard(app, client, 778)
    post_hook(client, _private_message("Запомни: код домофона 4512",
                                       user_id=778, message_id=2))

    result = post_hook(client, _private_message("Забудь про код домофона",
                                                user_id=778, message_id=3))

    assert result.json()["status"].startswith("admin_")


def test_secondary_user_cannot_purge_owner_memory_by_command(app, client):
    _onboard(app, client, 779)
    with app.state.session_factory() as session:
        from helm_core.knowledge.memory import try_remember
        owner_id = bind_knowledge_user(session, None)
        try_remember(session, channel="max", text="Запомни: код сейфа владельца 1234",
                     knowledge_user_id=owner_id)
        session.commit()

    result = post_hook(client, _private_message("Удали навсегда код сейфа",
                                                user_id=779, message_id=2))

    assert result.json()["status"] == "admin_not_found"
    with app.state.session_factory() as session:
        bind_knowledge_user(session, None)
        assert len(session.scalars(select(KnowledgeMemory)).all()) == 1
