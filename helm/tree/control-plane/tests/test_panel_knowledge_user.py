"""v3.8 §14.3, P8.6.5 (остаток) — вход KNOWLEDGE_USER в панель отдельным
enrollment-токеном и Knowledge-оболочка.

Решение владельца от 30.08.2026: доступ в панель secondary-пользователь
получает ОТДЕЛЬНЫМ токеном, не через Dedicated Knowledge Bot. Здесь
проверяется и сам путь, и то, что он не стал обходом owner-входа.

Крипта webauthn мокается (как в `test_panel_auth.py`) — проверяется
склейка, а не сторонняя библиотека.
"""

import hashlib
import tempfile
import uuid
from datetime import timedelta

import pytest
import webauthn
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from webauthn.authentication.verify_authentication_response import VerifiedAuthentication
from webauthn.registration.verify_registration_response import VerifiedRegistration

from helm_core.api.auth import _b64u
from helm_core.knowledge.tenancy import knowledge_principal
from helm_core.app import create_app
from helm_core.config import Settings
from helm_core.knowledge.memory import try_remember
from helm_core.knowledge.rls import apply_rls
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    Base, KnowledgeUser, KnowledgeUserRole, KnowledgeUserStatus, PanelEnrollmentToken,
    PanelSession, PanelStepUpChallenge, WebauthnCredential, utcnow,
)

from conftest import DB_URL, POLICY_PATH, SYSTEM_OWNER_ID, seed_system_owner

OWNER_ID = "tg:100500"
KU_CRED = b"ku-cred"
OWNER_CRED = b"owner-cred"
PLACEHOLDER = _b64u(b"placeholder")


@pytest.fixture
def app():
    engine = create_engine(DB_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_rls(conn)
    seed_system_owner(engine)
    settings = Settings(database_url=DB_URL, policy_path=POLICY_PATH, owner_id=OWNER_ID)
    application = create_app(settings, service_secret="test-service-secret")
    application.state.panel_auth_cookie_secret = "test-cookie-secret"
    return application


@pytest.fixture
def client(app):
    return TestClient(app, base_url="https://testserver")


def _make_active_user(app, **kwargs) -> uuid.UUID:
    with app.state.session_factory() as db:
        user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER,
                             status=KnowledgeUserStatus.ACTIVE, **kwargs)
        db.add(user)
        db.commit()
        return user.id


def _enrollment_token(app, principal: str, *, raw: str = "one-time-panel-token",
                      used: bool = False, expired: bool = False) -> str:
    with app.state.session_factory() as db:
        now = utcnow()
        db.add(PanelEnrollmentToken(
            token_hash=hashlib.sha256(raw.encode()).hexdigest(), owner_id=principal,
            expires_at=now + (timedelta(minutes=-1) if expired else timedelta(hours=1)),
            used_at=now if used else None,
        ))
        db.commit()
    return raw


def _mock_registration(monkeypatch, credential_id: bytes):
    monkeypatch.setattr(webauthn, "verify_registration_response", lambda **kw: VerifiedRegistration(
        credential_id=credential_id, credential_public_key=b"pub", sign_count=0,
        aaguid="", fmt="none", credential_type="public-key", user_verified=True,
        attestation_object=b"", credential_device_type="single_device", credential_backed_up=False,
    ))


def _mock_assertion(monkeypatch, credential_id: bytes):
    monkeypatch.setattr(webauthn, "verify_authentication_response",
                        lambda **kw: VerifiedAuthentication(
                            credential_id=credential_id, new_sign_count=1,
                            credential_device_type="single_device", credential_backed_up=False,
                            user_verified=True))


def _enroll(client, app, monkeypatch, token: str, credential_id: bytes = KU_CRED):
    started = client.post("/auth/knowledge/enroll/start", json={"enrollment_token": token})
    assert started.status_code == 200, started.text
    client.post("/auth/passkey/register/options", json={"enrollment_token": token})
    _mock_registration(monkeypatch, credential_id)
    return client.post("/auth/passkey/register/verify", json={
        "credential_id": _b64u(credential_id), "client_data": PLACEHOLDER,
        "attestation_object": PLACEHOLDER,
    })


# ── enrollment ───────────────────────────────────────────────────────────

def test_knowledge_user_enrolls_and_gets_session(app, client, monkeypatch):
    user_id = _make_active_user(app)
    token = _enrollment_token(app, knowledge_principal(user_id))

    result = _enroll(client, app, monkeypatch, token)

    assert result.status_code == 200, result.text
    with app.state.session_factory() as db:
        session_id = uuid.UUID(client.cookies["helm_panel_session"])
        assert db.get(PanelSession, session_id).owner_id == knowledge_principal(user_id)


def test_enroll_start_rejects_owner_token(app, client):
    """Токен владельца сюда не подходит: его путь — Telegram-виджет."""
    token = _enrollment_token(app, OWNER_ID)

    r = client.post("/auth/knowledge/enroll/start", json={"enrollment_token": token})

    assert r.status_code == 401


def test_enroll_start_rejects_used_and_expired_tokens(app, client):
    user_id = _make_active_user(app)
    used = _enrollment_token(app, knowledge_principal(user_id), raw="used-token", used=True)
    expired = _enrollment_token(app, knowledge_principal(user_id), raw="old-token", expired=True)

    assert client.post("/auth/knowledge/enroll/start",
                       json={"enrollment_token": used}).status_code == 401
    assert client.post("/auth/knowledge/enroll/start",
                       json={"enrollment_token": expired}).status_code == 401


def test_enroll_start_rejects_suspended_user(app, client):
    """Панель не должна становиться обходным входом для того, кому бот
    уже отказывает."""
    user_id = _make_active_user(app)
    with app.state.session_factory() as db:
        db.get(KnowledgeUser, user_id).status = KnowledgeUserStatus.SUSPENDED
        db.commit()
    token = _enrollment_token(app, knowledge_principal(user_id))

    r = client.post("/auth/knowledge/enroll/start", json={"enrollment_token": token})

    assert r.status_code == 403


def test_enrollment_token_is_one_use(app, client, monkeypatch):
    user_id = _make_active_user(app)
    token = _enrollment_token(app, knowledge_principal(user_id))
    assert _enroll(client, app, monkeypatch, token).status_code == 200

    second = TestClient(app, base_url="https://testserver")
    assert second.post("/auth/knowledge/enroll/start",
                       json={"enrollment_token": token}).status_code == 401


# ── usernameless-логин ───────────────────────────────────────────────────

def test_knowledge_user_logs_in_usernameless(app, client, monkeypatch):
    user_id = _make_active_user(app)
    _enroll(client, app, monkeypatch, _enrollment_token(app, knowledge_principal(user_id)))

    fresh = TestClient(app, base_url="https://testserver")
    fresh.post("/auth/knowledge/login/start")
    options = fresh.post("/auth/passkey/login/options")
    assert options.status_code == 200
    # Список чужих credential_id не выдаётся: опознавать пока некого.
    assert options.json()["allow_credentials"] == []

    _mock_assertion(monkeypatch, KU_CRED)
    verified = fresh.post("/auth/passkey/login/verify", json={
        "credential_id": _b64u(KU_CRED), "client_data": PLACEHOLDER,
        "authenticator_data": PLACEHOLDER, "signature": PLACEHOLDER,
    })

    assert verified.status_code == 200
    with app.state.session_factory() as db:
        session_id = uuid.UUID(fresh.cookies["helm_panel_session"])
        assert db.get(PanelSession, session_id).owner_id == knowledge_principal(user_id)


def test_usernameless_login_cannot_mint_an_owner_session(app, monkeypatch):
    """Главное свойство этого пути: он не должен становиться обходом
    Telegram-виджета для владельца — иначе одного владельческого passkey
    хватало бы вместо двух факторов."""
    with app.state.session_factory() as db:
        db.add(WebauthnCredential(owner_id=OWNER_ID, credential_id=OWNER_CRED,
                                  public_key=b"pub", sign_count=0))
        db.commit()

    client = TestClient(app, base_url="https://testserver")
    client.post("/auth/knowledge/login/start")
    client.post("/auth/passkey/login/options")
    _mock_assertion(monkeypatch, OWNER_CRED)

    r = client.post("/auth/passkey/login/verify", json={
        "credential_id": _b64u(OWNER_CRED), "client_data": PLACEHOLDER,
        "authenticator_data": PLACEHOLDER, "signature": PLACEHOLDER,
    })

    assert r.status_code == 403
    assert "helm_panel_session" not in client.cookies


# ── разделение ролей в панели ────────────────────────────────────────────

def _knowledge_session(app, user_id: uuid.UUID) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    with app.state.session_factory() as db:
        record = PanelSession(owner_id=knowledge_principal(user_id),
                              expires_at=utcnow() + timedelta(hours=1))
        db.add(record)
        db.commit()
        client.cookies.set("helm_panel_session", str(record.id))
    return client


def _owner_session(app) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    with app.state.session_factory() as db:
        record = PanelSession(owner_id=OWNER_ID, expires_at=utcnow() + timedelta(hours=1))
        db.add(record)
        db.commit()
        client.cookies.set("helm_panel_session", str(record.id))
    return client


@pytest.mark.parametrize("path", ["/today", "/approvals", "/tasks", "/money", "/system", "/users"])
def test_knowledge_user_is_refused_owner_sections(app, path):
    """§14.3 "Knowledge-only panel shell" — отказом сервера, а не тем, что
    фронт не нарисовал пункт меню."""
    ku = _knowledge_session(app, _make_active_user(app))

    assert ku.get(f"/api/panel/v1{path}").status_code == 403


def test_knowledge_user_cannot_approve(app):
    ku = _knowledge_session(app, _make_active_user(app))

    r = ku.post(f"/api/panel/v1/actions/{uuid.uuid4()}/approve")

    assert r.status_code == 403


def test_knowledge_user_cannot_invite_or_suspend_others(app):
    victim = _make_active_user(app)
    ku = _knowledge_session(app, _make_active_user(app))

    assert ku.post("/api/panel/v1/users/invite", json={}).status_code == 403
    assert ku.post(f"/api/panel/v1/users/{victim}/suspend").status_code == 403


# ── Knowledge-оболочка ───────────────────────────────────────────────────

def test_knowledge_shell_shows_only_own_memories(app):
    user_id = _make_active_user(app, display_name="Аня")
    with app.state.session_factory() as db:
        owner_id = bind_knowledge_user(db, None)
        try_remember(db, channel="max", text="Запомни: секрет владельца 1234",
                     knowledge_user_id=owner_id)
        try_remember(db, channel="telegram_knowledge", text="Запомни: моя заметка про кота",
                     knowledge_user_id=user_id)
        db.commit()

    body = _knowledge_session(app, user_id).get("/api/panel/v1/knowledge").json()

    texts = [m["text"] for m in body["memories"]]
    assert texts == ["моя заметка про кота"]
    assert "секрет владельца 1234" not in str(body)
    assert body["role"] == KnowledgeUserRole.KNOWLEDGE_USER
    assert body["display_name"] == "Аня"


def test_knowledge_shell_for_owner_shows_owner_corpus(app):
    user_id = _make_active_user(app)
    with app.state.session_factory() as db:
        owner_id = bind_knowledge_user(db, None)
        try_remember(db, channel="max", text="Запомни: заметка владельца",
                     knowledge_user_id=owner_id)
        try_remember(db, channel="telegram_knowledge", text="Запомни: чужая заметка",
                     knowledge_user_id=user_id)
        db.commit()

    body = _owner_session(app).get("/api/panel/v1/knowledge").json()

    assert [m["text"] for m in body["memories"]] == ["заметка владельца"]
    assert body["role"] == KnowledgeUserRole.SYSTEM_OWNER


def test_knowledge_shell_requires_a_session(app):
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get("/api/panel/v1/knowledge").status_code == 401


# ── выдача токена панели владельцем ──────────────────────────────────────

def _stepup(app, client, scope: str) -> dict[str, str]:
    with app.state.session_factory() as db:
        session_id = uuid.UUID(client.cookies["helm_panel_session"])
        challenge = PanelStepUpChallenge(
            session_id=session_id, action_hashes=[scope], approval_ids=[],
            challenge=b"challenge", expires_at=utcnow() + timedelta(seconds=60))
        db.add(challenge)
        db.commit()
        return {"X-Helm-Stepup": str(challenge.id)}


def test_owner_issues_panel_token_for_active_user(app):
    user_id = _make_active_user(app)
    owner = _owner_session(app)

    r = owner.post(f"/api/panel/v1/users/{user_id}/panel-invite",
                   headers=_stepup(app, owner, f"panel:users:panel-invite:{user_id}"))

    assert r.status_code == 201
    assert len(r.json()["enrollment_token"]) > 20
    with app.state.session_factory() as db:
        from sqlalchemy import select
        token = db.scalars(select(PanelEnrollmentToken)).one()
        assert token.owner_id == knowledge_principal(user_id)


def test_panel_token_refused_for_user_who_never_joined_the_bot(app):
    with app.state.session_factory() as db:
        user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER,
                             status=KnowledgeUserStatus.INVITED)
        db.add(user)
        db.commit()
        user_id = user.id
    owner = _owner_session(app)

    r = owner.post(f"/api/panel/v1/users/{user_id}/panel-invite",
                   headers=_stepup(app, owner, f"panel:users:panel-invite:{user_id}"))

    assert r.status_code == 409


def test_panel_token_scope_is_bound_to_the_target_user(app):
    victim = _make_active_user(app)
    other = _make_active_user(app)
    owner = _owner_session(app)

    r = owner.post(f"/api/panel/v1/users/{victim}/panel-invite",
                   headers=_stepup(app, owner, f"panel:users:panel-invite:{other}"))

    assert r.status_code == 403


def test_owner_cannot_issue_a_panel_token_for_himself(app):
    owner = _owner_session(app)

    r = owner.post(f"/api/panel/v1/users/{SYSTEM_OWNER_ID}/panel-invite",
                   headers=_stepup(app, owner, f"panel:users:panel-invite:{SYSTEM_OWNER_ID}"))

    assert r.status_code == 409


# ── §14.3 «panel sessions revoked» при приостановке ──────────────────────

def test_suspend_revokes_live_panel_session(app):
    """До появления входа в панель отзывать было нечего. Теперь сессия
    живёт до суток, и не отозвать её — значит оставить приостановленному
    человеку сутки чтения."""
    user_id = _make_active_user(app)
    ku = _knowledge_session(app, user_id)
    assert ku.get("/api/panel/v1/knowledge").status_code == 200

    owner = _owner_session(app)
    owner.post(f"/api/panel/v1/users/{user_id}/suspend",
               headers=_stepup(app, owner, f"panel:users:suspend:{user_id}"))

    assert ku.get("/api/panel/v1/knowledge").status_code == 401


def test_suspend_burns_unused_panel_enrollment_token(app):
    """Токен, выданный до приостановки, не должен превращаться в новый
    вход после неё."""
    user_id = _make_active_user(app)
    owner = _owner_session(app)
    issued = owner.post(f"/api/panel/v1/users/{user_id}/panel-invite",
                        headers=_stepup(app, owner, f"panel:users:panel-invite:{user_id}"))
    token = issued.json()["enrollment_token"]

    owner.post(f"/api/panel/v1/users/{user_id}/suspend",
               headers=_stepup(app, owner, f"panel:users:suspend:{user_id}"))

    fresh = TestClient(app, base_url="https://testserver")
    assert fresh.post("/auth/knowledge/enroll/start",
                      json={"enrollment_token": token}).status_code == 401


def test_reset_passkey_revokes_credentials_and_sessions(app, monkeypatch):
    """Потерянное устройство: passkey не перевыпускается (приватный ключ
    серверу неизвестен), поэтому сброс — отзыв credential'ов."""
    user_id = _make_active_user(app)
    enrolled = TestClient(app, base_url="https://testserver")
    _enroll(enrolled, app, monkeypatch, _enrollment_token(app, knowledge_principal(user_id)))
    assert enrolled.get("/api/panel/v1/knowledge").status_code == 200

    owner = _owner_session(app)
    r = owner.post(f"/api/panel/v1/users/{user_id}/reset-passkey",
                   headers=_stepup(app, owner, f"panel:users:reset-passkey:{user_id}"))

    assert r.status_code == 200
    assert enrolled.get("/api/panel/v1/knowledge").status_code == 401

    # Старым passkey войти больше нельзя — только по новому токену владельца.
    fresh = TestClient(app, base_url="https://testserver")
    fresh.post("/auth/knowledge/login/start")
    fresh.post("/auth/passkey/login/options")
    _mock_assertion(monkeypatch, KU_CRED)
    denied = fresh.post("/auth/passkey/login/verify", json={
        "credential_id": _b64u(KU_CRED), "client_data": PLACEHOLDER,
        "authenticator_data": PLACEHOLDER, "signature": PLACEHOLDER,
    })
    assert denied.status_code == 401


def test_reset_passkey_scope_is_bound_to_the_target_user(app):
    victim = _make_active_user(app)
    other = _make_active_user(app)
    owner = _owner_session(app)

    r = owner.post(f"/api/panel/v1/users/{victim}/reset-passkey",
                   headers=_stepup(app, owner, f"panel:users:reset-passkey:{other}"))

    assert r.status_code == 403


# ── §14.3 offboarding: suspend → export → delete ─────────────────────────

def _fill_vault(app, user_id):
    from helm_core.knowledge.memory import try_remember
    with app.state.session_factory() as db:
        try_remember(db, channel="telegram_knowledge", text="Запомни: код 4512",
                     knowledge_user_id=user_id)
        db.commit()


def test_export_returns_a_path_not_the_contents(app):
    """Владелец получает файл, чтобы отдать его человеку, но панель чужой
    Второй мозг не показывает (§14.3 «no normal content browser»)."""
    user_id = _make_active_user(app)
    _fill_vault(app, user_id)
    owner = _owner_session(app)

    r = owner.post(f"/api/panel/v1/users/{user_id}/export",
                   headers=_stepup(app, owner, f"panel:users:export:{user_id}"))

    assert r.status_code == 201
    body = r.json()
    assert body["archive_path"].endswith(".zip")
    assert body["memories"] == 1
    assert body["backup_retention"]
    # Ни одной записи памяти в самом ответе.
    assert "4512" not in str(body)


def test_delete_refuses_while_user_is_still_active(app):
    user_id = _make_active_user(app)
    owner = _owner_session(app)

    r = owner.post(f"/api/panel/v1/users/{user_id}/delete", json={"export_taken": True},
                   headers=_stepup(app, owner, f"panel:users:delete:{user_id}"))

    assert r.status_code == 409
    assert "приостанов" in r.text


def test_delete_refuses_without_an_explicit_answer_about_the_export(app):
    """Отказ от выгрузки — тоже решение, но его надо принять, а не
    проскочить."""
    user_id = _make_active_user(app)
    owner = _owner_session(app)
    owner.post(f"/api/panel/v1/users/{user_id}/suspend",
               headers=_stepup(app, owner, f"panel:users:suspend:{user_id}"))

    r = owner.post(f"/api/panel/v1/users/{user_id}/delete", json={"export_taken": False},
                   headers=_stepup(app, owner, f"panel:users:delete:{user_id}"))

    assert r.status_code == 409
    with app.state.session_factory() as db:
        assert db.get(KnowledgeUser, user_id).status == KnowledgeUserStatus.SUSPENDED


def test_full_offboarding_sequence(app):
    user_id = _make_active_user(app)
    _fill_vault(app, user_id)
    owner = _owner_session(app)

    owner.post(f"/api/panel/v1/users/{user_id}/suspend",
               headers=_stepup(app, owner, f"panel:users:suspend:{user_id}"))
    exported = owner.post(f"/api/panel/v1/users/{user_id}/export",
                          headers=_stepup(app, owner, f"panel:users:export:{user_id}"))
    deleted = owner.post(f"/api/panel/v1/users/{user_id}/delete",
                         json={"export_taken": True},
                         headers=_stepup(app, owner, f"panel:users:delete:{user_id}"))

    assert exported.status_code == 201
    assert deleted.status_code == 200
    assert deleted.json()["backup_retention"]
    with app.state.session_factory() as db:
        assert db.get(KnowledgeUser, user_id).status == KnowledgeUserStatus.DELETED


def test_delete_scope_is_bound_to_the_target_user(app):
    victim = _make_active_user(app)
    other = _make_active_user(app)
    owner = _owner_session(app)
    owner.post(f"/api/panel/v1/users/{victim}/suspend",
               headers=_stepup(app, owner, f"panel:users:suspend:{victim}"))

    r = owner.post(f"/api/panel/v1/users/{victim}/delete", json={"export_taken": True},
                   headers=_stepup(app, owner, f"panel:users:delete:{other}"))

    assert r.status_code == 403
    with app.state.session_factory() as db:
        assert db.get(KnowledgeUser, victim).status == KnowledgeUserStatus.SUSPENDED


def test_owner_account_cannot_be_deleted(app):
    owner = _owner_session(app)

    r = owner.post(f"/api/panel/v1/users/{SYSTEM_OWNER_ID}/delete",
                   json={"export_taken": True},
                   headers=_stepup(app, owner, f"panel:users:delete:{SYSTEM_OWNER_ID}"))

    assert r.status_code == 409


def test_knowledge_user_cannot_export_or_delete_anyone(app):
    victim = _make_active_user(app)
    ku = _knowledge_session(app, _make_active_user(app))

    assert ku.post(f"/api/panel/v1/users/{victim}/export").status_code == 403
    assert ku.post(f"/api/panel/v1/users/{victim}/delete",
                   json={"export_taken": True}).status_code == 403


# ── §14.15: выдача оригинала документа ───────────────────────────────────

PDF = b"%PDF-1.4 owner contract"


def _upload_for(app, user_id, *, name="contract.pdf", data=PDF):
    import hashlib
    from pathlib import Path
    from helm_core.knowledge.ingest import register_file_for_ingest
    with app.state.session_factory() as db:
        raw_dir = Path(tempfile.mkdtemp())
        raw_path = raw_dir / f"{hashlib.sha256(data).hexdigest()}-{name}"
        raw_path.write_bytes(data)
        result = register_file_for_ingest(
            db, domain="engineering", raw_path=raw_path, original_filename=name,
            mime_type="application/pdf", vault_root=str(raw_dir / "vault"),
            knowledge_user_id=user_id)
        db.commit()
        return result.source.id


def test_owner_downloads_the_exact_original_bytes(app):
    owner = _owner_session(app)
    with app.state.session_factory() as db:
        owner_tenant = bind_knowledge_user(db, None)
    source_id = _upload_for(app, owner_tenant)

    r = owner.post(f"/api/panel/v1/knowledge/sources/{source_id}/download",
                   headers=_stepup(app, owner, f"panel:knowledge:download:{source_id}"))

    assert r.status_code == 200
    assert r.content == PDF
    assert "contract.pdf" in r.headers["Content-Disposition"]
    assert r.headers["X-Helm-Sha256"] == hashlib.sha256(PDF).hexdigest()


def test_download_requires_a_fresh_passkey(app):
    owner = _owner_session(app)
    with app.state.session_factory() as db:
        owner_tenant = bind_knowledge_user(db, None)
    source_id = _upload_for(app, owner_tenant)

    assert owner.post(
        f"/api/panel/v1/knowledge/sources/{source_id}/download").status_code == 401


def test_wrong_user_cannot_download_someone_elses_document(app):
    """Прямое требование владельца: проверять принадлежность на каждом
    скачивании. До этого эндпоинта проверять было нечего."""
    with app.state.session_factory() as db:
        owner_tenant = bind_knowledge_user(db, None)
    source_id = _upload_for(app, owner_tenant)
    ku = _knowledge_session(app, _make_active_user(app))

    r = ku.post(f"/api/panel/v1/knowledge/sources/{source_id}/download",
                headers=_stepup(app, ku, f"panel:knowledge:download:{source_id}"))

    assert r.status_code == 404
    assert r.content != PDF


def test_download_scope_is_bound_to_the_document(app):
    owner = _owner_session(app)
    with app.state.session_factory() as db:
        owner_tenant = bind_knowledge_user(db, None)
    source_id = _upload_for(app, owner_tenant)
    other_id = uuid.uuid4()

    r = owner.post(f"/api/panel/v1/knowledge/sources/{source_id}/download",
                   headers=_stepup(app, owner, f"panel:knowledge:download:{other_id}"))

    assert r.status_code == 403


def test_source_search_is_scoped_to_the_session(app):
    with app.state.session_factory() as db:
        owner_tenant = bind_knowledge_user(db, None)
    _upload_for(app, owner_tenant, name="owner-secret.pdf")
    ku = _knowledge_session(app, _make_active_user(app))

    assert ku.get("/api/panel/v1/knowledge/sources?q=owner-secret").json()["items"] == []
    owner = _owner_session(app)
    assert len(owner.get("/api/panel/v1/knowledge/sources?q=owner-secret").json()["items"]) == 1
