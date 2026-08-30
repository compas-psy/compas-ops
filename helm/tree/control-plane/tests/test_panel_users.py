"""v3.8 §14.3, P8.6.5 — «Система → Пользователи» в панели владельца.

Passkey-церемония здесь не переигрывается (это `test_panel_auth.py`):
сессия и step-up-challenge создаются строками в БД, чтобы тесты
проверяли именно раздел «Пользователи», а не webauthn ещё раз.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from helm_core.api.panel import SCOPE_INVITE
from helm_core.app import create_app
from helm_core.config import Settings
from helm_core.knowledge.onboarding import consume_invite, create_invite
from helm_core.knowledge.rls import apply_rls
from helm_core.models import (
    Base, KnowledgeUser, KnowledgeUserRole, KnowledgeUserStatus, KnowledgeUserUsage,
    PanelSession, PanelStepUpChallenge, utcnow,
)

from conftest import DB_URL, POLICY_PATH, SYSTEM_OWNER_ID, seed_system_owner

BOT_USERNAME = "helm_knowledge_bot"


@pytest.fixture
def app():
    engine = create_engine(DB_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_rls(conn)
    seed_system_owner(engine)
    settings = Settings(database_url=DB_URL, policy_path=POLICY_PATH, owner_id="tg:100500",
                        knowledge_telegram_bot_username=BOT_USERNAME)
    application = create_app(settings, service_secret="test-service-secret")
    application.state.panel_auth_cookie_secret = "test-cookie-secret"
    return application


@pytest.fixture
def client(app):
    client = TestClient(app, base_url="https://testserver")
    with app.state.session_factory() as db:
        session = PanelSession(owner_id="tg:100500", expires_at=utcnow() + timedelta(hours=1))
        db.add(session)
        db.commit()
        client.cookies.set("helm_panel_session", str(session.id))
        client.__dict__["_panel_session_id"] = session.id
    return client


def _stepup(app, client, scope: str) -> dict[str, str]:
    """Свежий одноразовый challenge под КОНКРЕТНУЮ операцию — ровно то,
    что потребляет `require_stepup()` и проверяет `assert_scope()`."""
    with app.state.session_factory() as db:
        challenge = PanelStepUpChallenge(
            session_id=client.__dict__["_panel_session_id"], action_hashes=[scope],
            approval_ids=[], challenge=b"challenge",
            expires_at=utcnow() + timedelta(seconds=60),
        )
        db.add(challenge)
        db.commit()
        return {"X-Helm-Stepup": str(challenge.id)}


def _make_user(app, **kwargs) -> uuid.UUID:
    with app.state.session_factory() as db:
        user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER, **kwargs)
        db.add(user)
        db.commit()
        return user.id


# ── GET /users ───────────────────────────────────────────────────────────

def test_list_users_requires_session(app):
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get("/api/panel/v1/users").status_code == 401


def test_list_users_returns_owner_and_secondary(app, client):
    _make_user(app, display_name="Аня", storage_quota_bytes=1024)

    body = client.get("/api/panel/v1/users").json()

    roles = {item["role"] for item in body["items"]}
    assert roles == {KnowledgeUserRole.SYSTEM_OWNER, KnowledgeUserRole.KNOWLEDGE_USER}
    secondary = next(i for i in body["items"] if i["role"] == KnowledgeUserRole.KNOWLEDGE_USER)
    assert secondary["display_name"] == "Аня"
    assert secondary["storage_quota_bytes"] == 1024
    assert secondary["allow_paid_ai"] is False


def test_list_users_reports_usage(app, client):
    user_id = _make_user(app)
    with app.state.session_factory() as db:
        db.add(KnowledgeUserUsage(knowledge_user_id=user_id, storage_bytes=777,
                                  ingest_bytes_today=55, sources_count=3, memories_count=9))
        db.commit()

    item = next(i for i in client.get("/api/panel/v1/users").json()["items"]
                if i["id"] == str(user_id))

    assert item["storage_bytes"] == 777
    assert item["ingest_bytes_today"] == 55
    assert item["sources_count"] == 3
    assert item["memories_count"] == 9
    # `queued_jobs` вычисляется на лету (`check_queue_depth`) — копия в
    # ответе была бы вторым источником правды о том же.
    assert "queued_jobs" not in item


def test_list_users_never_exposes_content_or_telegram_id(app, client):
    """§14.3 "no normal content browser across users" — раздел управляет
    людьми, а не читает их Второй мозг."""
    with app.state.session_factory() as db:
        result = create_invite(db, created_by="owner")
        user_id = result.user.id
        consume_invite(db, raw_token=result.raw_token, channel="telegram_knowledge",
                       external_user_id="4242", external_chat_id="4242")
        db.commit()

    item = next(i for i in client.get("/api/panel/v1/users").json()["items"]
                if i["id"] == str(user_id))

    assert item["channels"] == [
        {"channel": "telegram_knowledge", "verified_at": item["channels"][0]["verified_at"],
         "is_primary": True}
    ]
    # Telegram-идентификатор живого человека владельцу для управления
    # учёткой не нужен и не отдаётся.
    assert "4242" not in str(item)


# ── POST /users/invite ───────────────────────────────────────────────────

def test_invite_requires_stepup(client):
    r = client.post("/api/panel/v1/users/invite", json={})
    assert r.status_code == 401


def test_invite_creates_user_and_returns_deep_link_once(app, client):
    r = client.post("/api/panel/v1/users/invite", json={"display_name": "Аня"},
                    headers=_stepup(app, client, SCOPE_INVITE))

    assert r.status_code == 201
    body = r.json()
    assert body["deep_link"].startswith(f"https://t.me/{BOT_USERNAME}?start=kb_")
    assert len(body["invite_token"]) > 20

    with app.state.session_factory() as db:
        user = db.get(KnowledgeUser, uuid.UUID(body["knowledge_user_id"]))
        assert user.status == KnowledgeUserStatus.INVITED
        assert user.role == KnowledgeUserRole.KNOWLEDGE_USER


def test_stepup_challenge_is_single_use(app, client):
    headers = _stepup(app, client, SCOPE_INVITE)
    first = client.post("/api/panel/v1/users/invite", json={}, headers=headers)
    second = client.post("/api/panel/v1/users/invite", json={}, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 401


# ── suspend / reactivate ─────────────────────────────────────────────────

def test_suspend_and_reactivate_secondary_user(app, client):
    user_id = _make_user(app)

    suspended = client.post(f"/api/panel/v1/users/{user_id}/suspend",
                            headers=_stepup(app, client, f"panel:users:suspend:{user_id}"))
    assert suspended.status_code == 200
    with app.state.session_factory() as db:
        assert db.get(KnowledgeUser, user_id).status == KnowledgeUserStatus.SUSPENDED

    back = client.post(f"/api/panel/v1/users/{user_id}/reactivate",
                       headers=_stepup(app, client, f"panel:users:reactivate:{user_id}"))
    assert back.status_code == 200
    with app.state.session_factory() as db:
        assert db.get(KnowledgeUser, user_id).status == KnowledgeUserStatus.ACTIVE


def test_owner_cannot_be_suspended_through_users_section(app, client):
    """Suspend владельца закрыл бы ему доступ к собственному HELM'у из
    интерфейса, задуманного для чужих учёток, и откатить это было бы
    уже нечем."""
    r = client.post(f"/api/panel/v1/users/{SYSTEM_OWNER_ID}/suspend",
                    headers=_stepup(app, client, f"panel:users:suspend:{SYSTEM_OWNER_ID}"))

    assert r.status_code == 409
    with app.state.session_factory() as db:
        assert db.get(KnowledgeUser, SYSTEM_OWNER_ID).status == KnowledgeUserStatus.ACTIVE


def test_suspend_unknown_user_is_404(app, client):
    unknown = uuid.uuid4()
    r = client.post(f"/api/panel/v1/users/{unknown}/suspend",
                    headers=_stepup(app, client, f"panel:users:suspend:{unknown}"))
    assert r.status_code == 404


def test_suspend_requires_stepup(client):
    r = client.post(f"/api/panel/v1/users/{uuid.uuid4()}/suspend")
    assert r.status_code == 401


# ── квоты ────────────────────────────────────────────────────────────────

def test_set_quota_updates_only_given_fields(app, client):
    user_id = _make_user(app, storage_quota_bytes=100, daily_ingest_quota_bytes=200)

    r = client.post(f"/api/panel/v1/users/{user_id}/quota",
                    json={"storage_quota_bytes": 999},
                    headers=_stepup(app, client, f"panel:users:quota:{user_id}"))

    assert r.status_code == 200
    with app.state.session_factory() as db:
        user = db.get(KnowledgeUser, user_id)
        assert user.storage_quota_bytes == 999
        # Не переданное поле не обнулилось.
        assert user.daily_ingest_quota_bytes == 200


def test_set_quota_can_remove_limit(app, client):
    user_id = _make_user(app, storage_quota_bytes=100)

    client.post(f"/api/panel/v1/users/{user_id}/quota",
                json={"storage_quota_bytes": None},
                headers=_stepup(app, client, f"panel:users:quota:{user_id}"))

    with app.state.session_factory() as db:
        assert db.get(KnowledgeUser, user_id).storage_quota_bytes is None


def test_owner_quota_is_not_editable_here(app, client):
    r = client.post(f"/api/panel/v1/users/{SYSTEM_OWNER_ID}/quota",
                    json={"storage_quota_bytes": 1},
                    headers=_stepup(app, client, f"panel:users:quota:{SYSTEM_OWNER_ID}"))
    assert r.status_code == 409


# ── §10.5.8.1 привязка церемонии к операции ──────────────────────────────

def test_stepup_for_invite_cannot_suspend(app, client):
    """Подтверждение, полученное на «пригласить», не годится для
    «приостановить» — иначе одна церемония открывала бы любую операцию
    раздела."""
    user_id = _make_user(app)

    r = client.post(f"/api/panel/v1/users/{user_id}/suspend",
                    headers=_stepup(app, client, SCOPE_INVITE))

    assert r.status_code == 403
    with app.state.session_factory() as db:
        assert db.get(KnowledgeUser, user_id).status == KnowledgeUserStatus.ACTIVE


def test_stepup_for_one_user_cannot_suspend_another(app, client):
    victim = _make_user(app)
    other = _make_user(app)

    r = client.post(f"/api/panel/v1/users/{victim}/suspend",
                    headers=_stepup(app, client, f"panel:users:suspend:{other}"))

    assert r.status_code == 403
    with app.state.session_factory() as db:
        assert db.get(KnowledgeUser, victim).status == KnowledgeUserStatus.ACTIVE
