"""Panel auth (ТЗ §10.5.6-§10.5.8.1): Telegram Login Widget, первый enrollment,
passkey-логин, passkey step-up.

Крипто самих webauthn здесь не перепроверяется (сторонняя, хорошо
протестированная библиотека) — тестируется склейка: владелец против не-владелец,
однократность enrollment-токена и step-up challenge, привязка challenge к
session_id, переходные cookie между Telegram-подтверждением и passkey-церемонией.
Подпись Telegram Login Widget — HMAC, написанный в этом же репозитории
(verify_login_widget), поэтому её тесты считают реальную подпись, а не мокают.
"""

import hashlib
import hmac as hmac_mod
import time
from datetime import timedelta

import pytest
import webauthn
from fastapi.testclient import TestClient
from webauthn.authentication.verify_authentication_response import VerifiedAuthentication
from webauthn.registration.verify_registration_response import VerifiedRegistration

from helm_core.api.auth import _b64u
from helm_core.app import create_app
from helm_core.config import Settings
from helm_core.knowledge.rls import apply_rls
from helm_core.models import Base, PanelEnrollmentToken, PanelSession, WebauthnCredential, utcnow

from conftest import DB_URL, POLICY_PATH, seed_system_owner

BOT_TOKEN = "test-bot-token"
#: Без префикса "tg:" — Telegram Login Widget присылает голый числовой id
#: (hermes/plugins/helm-control тоже шлёт str(chat_id) без префикса в
#: /internal/inbound), settings.owner_id сравнивается как есть, без
#: нормализации (ingest.py). Это НЕ то же самое, что OWNER_ID="tg:100500" в
#: conftest.py — та константа используется другими тестами для владельца
#: внутренних Control Plane вызовов (/internal/*), а не Telegram-виджета.
OWNER_ID = "100500"
NOT_OWNER_SUB = "999999"
OWNER_SUB = OWNER_ID
STEP_CRED_B64 = _b64u(b"step-cred")
CRED_X_B64 = _b64u(b"cred-x")
PLACEHOLDER_B64 = _b64u(b"placeholder")


def _widget_fields(user_id: str, *, auth_date: int | None = None,
                   bot_token: str = BOT_TOKEN, tamper_hash: bool = False) -> dict[str, str]:
    """Собрать реально подписанные поля Telegram Login Widget для теста."""
    payload = {"id": user_id, "first_name": "Owner",
               "auth_date": str(auth_date if auth_date is not None else int(time.time()))}
    data_check_string = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    digest = hmac_mod.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return {**payload, "hash": ("0" * 64) if tamper_hash else digest}


@pytest.fixture
def app_and_client():
    from sqlalchemy import create_engine
    engine = create_engine(DB_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # Этот файл не трогает Knowledge-таблицы, но drop_all/create_all на
    # ТОЙ ЖЕ физической БД, что использует helm_test для всех остальных
    # тестов, иначе оставлял бы их без RLS/SYSTEM_OWNER до следующего
    # фикстуры, которая это восстановит — независимо от порядка запуска.
    with engine.begin() as conn:
        apply_rls(conn)
    seed_system_owner(engine)
    settings = Settings(database_url=DB_URL, policy_path=POLICY_PATH, owner_id=OWNER_ID)
    app = create_app(settings, service_secret="test-service-secret", telegram_bot_token=BOT_TOKEN)
    app.state.panel_auth_cookie_secret = "test-cookie-secret"
    # base_url на https: pending- и session-cookie ставятся с Secure (верно для
    # прода за Caddy), а httpx-клиент по умолчанию гоняет http://testserver и
    # тихо не пересылал бы Secure-куки обратно.
    return app, TestClient(app, base_url="https://testserver")


@pytest.fixture
def client(app_and_client):
    return app_and_client[1]


@pytest.fixture
def app(app_and_client):
    return app_and_client[0]


def _db_session(app):
    return app.state.session_factory()


# ── Telegram Login Widget (§10.5.6) ─────────────────────────────────────────

def test_callback_rejects_bad_signature(client):
    fields = _widget_fields(OWNER_SUB, tamper_hash=True)
    r = client.get("/auth/telegram/callback", params=fields, follow_redirects=False)
    assert r.status_code == 401


def test_callback_rejects_stale_auth_date(client):
    fields = _widget_fields(OWNER_SUB, auth_date=int(time.time()) - 3600)
    r = client.get("/auth/telegram/callback", params=fields, follow_redirects=False)
    assert r.status_code == 401


def test_callback_wrong_owner_is_rejected(client):
    fields = _widget_fields(NOT_OWNER_SUB)
    r = client.get("/auth/telegram/callback", params=fields, follow_redirects=False)
    assert r.status_code == 403


def test_callback_success_no_credential_redirects_to_enroll(client):
    fields = _widget_fields(OWNER_SUB)
    r = client.get("/auth/telegram/callback", params=fields, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?step=enroll"


def test_callback_success_with_credential_redirects_to_login(app, client):
    with _db_session(app) as db:
        db.add(WebauthnCredential(owner_id=OWNER_ID, credential_id=b"cred-1",
                                  public_key=b"pub-1", sign_count=0))
        db.commit()

    fields = _widget_fields(OWNER_SUB)
    r = client.get("/auth/telegram/callback", params=fields, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?step=login"


def _do_telegram_login(client, user_id: str = OWNER_SUB) -> None:
    """Проводит подтверждение через Telegram, оставляя pending-cookie в клиенте."""
    client.get("/auth/telegram/callback", params=_widget_fields(user_id), follow_redirects=False)


# ── первый enrollment (§10.5.7) ─────────────────────────────────────────────

def _make_enrollment_token(app, raw: str = "one-time-token", *, owner_id: str = OWNER_ID,
                           used: bool = False, expired: bool = False) -> None:
    with _db_session(app) as db:
        now = utcnow()
        db.add(PanelEnrollmentToken(
            token_hash=hashlib.sha256(raw.encode()).hexdigest(), owner_id=owner_id,
            expires_at=now + (timedelta(minutes=-1) if expired else timedelta(minutes=30)),
            used_at=now if used else None,
        ))
        db.commit()


def test_register_options_rejects_bad_token(app, client):
    _do_telegram_login(client)
    _make_enrollment_token(app, "real-token")
    r = client.post("/auth/passkey/register/options", json={"enrollment_token": "неверный"})
    assert r.status_code == 401


def test_register_options_rejects_expired_token(app, client):
    _do_telegram_login(client)
    _make_enrollment_token(app, "real-token", expired=True)
    r = client.post("/auth/passkey/register/options", json={"enrollment_token": "real-token"})
    assert r.status_code == 401


def test_register_options_rejects_token_for_other_owner(app, client):
    _do_telegram_login(client)
    _make_enrollment_token(app, "real-token", owner_id="tg:другой")
    r = client.post("/auth/passkey/register/options", json={"enrollment_token": "real-token"})
    assert r.status_code == 401


def test_register_options_without_pending_login_is_rejected(client):
    r = client.post("/auth/passkey/register/options", json={"enrollment_token": "x"})
    assert r.status_code == 401


def test_full_enrollment_creates_credential_and_session(app, client, monkeypatch):
    _do_telegram_login(client)
    _make_enrollment_token(app, "real-token")

    options = client.post("/auth/passkey/register/options", json={"enrollment_token": "real-token"})
    assert options.status_code == 200, options.text

    monkeypatch.setattr(webauthn, "verify_registration_response", lambda **kwargs: VerifiedRegistration(
        credential_id=b"new-cred-id", credential_public_key=b"new-pub-key", sign_count=0,
        aaguid="", fmt="none", credential_type="public-key", user_verified=True,
        attestation_object=b"", credential_device_type="single_device", credential_backed_up=False,
    ))

    verify = client.post("/auth/passkey/register/verify", json={
        "credential_id": PLACEHOLDER_B64, "client_data": PLACEHOLDER_B64,
        "attestation_object": PLACEHOLDER_B64,
    })
    assert verify.status_code == 200, verify.text
    assert "helm_panel_session" in verify.cookies

    with _db_session(app) as db:
        cred = db.query(WebauthnCredential).filter_by(owner_id=OWNER_ID).one()
        assert cred.credential_id == b"new-cred-id"
        token = db.query(PanelEnrollmentToken).filter_by(owner_id=OWNER_ID).one()
        assert token.used_at is not None


def test_enrollment_token_cannot_be_reused(app, client, monkeypatch):
    _do_telegram_login(client)
    _make_enrollment_token(app, "real-token")
    client.post("/auth/passkey/register/options", json={"enrollment_token": "real-token"})
    monkeypatch.setattr(webauthn, "verify_registration_response", lambda **kwargs: VerifiedRegistration(
        credential_id=b"cred-a", credential_public_key=b"pub-a", sign_count=0,
        aaguid="", fmt="none", credential_type="public-key", user_verified=True,
        attestation_object=b"", credential_device_type="single_device", credential_backed_up=False,
    ))
    first = client.post("/auth/passkey/register/verify",
                        json={"credential_id": PLACEHOLDER_B64, "client_data": PLACEHOLDER_B64,
                              "attestation_object": PLACEHOLDER_B64})
    assert first.status_code == 200

    # Второй заход тем же токеном: Telegram заново подтверждает, но токен уже сожжён.
    _do_telegram_login(client)
    reuse = client.post("/auth/passkey/register/options", json={"enrollment_token": "real-token"})
    assert reuse.status_code == 401


# ── passkey-логин (после первого enrollment) ────────────────────────────────

def test_login_options_requires_existing_credential(client):
    _do_telegram_login(client)  # без credential в БД → шаг сервера всё равно "enroll",
    # но проверим прямой вызов login/options без прохождения enroll-редиректа:
    r = client.post("/auth/passkey/login/options")
    assert r.status_code == 401


def test_full_login_creates_session_and_revokes_previous(app, client, monkeypatch):
    with _db_session(app) as db:
        db.add(WebauthnCredential(owner_id=OWNER_ID, credential_id=b"cred-x",
                                  public_key=b"pub-x", sign_count=5))
        old_session = PanelSession(owner_id=OWNER_ID, expires_at=utcnow() + timedelta(hours=1))
        db.add(old_session)
        db.commit()
        old_session_id = old_session.id

    _do_telegram_login(client)
    options = client.post("/auth/passkey/login/options")
    assert options.status_code == 200, options.text

    monkeypatch.setattr(webauthn, "verify_authentication_response", lambda **kwargs: VerifiedAuthentication(
        credential_id=b"cred-x", new_sign_count=6,
        credential_device_type="single_device", credential_backed_up=False, user_verified=True,
    ))
    verify = client.post("/auth/passkey/login/verify", json={
        "credential_id": CRED_X_B64, "client_data": PLACEHOLDER_B64,
        "authenticator_data": PLACEHOLDER_B64, "signature": PLACEHOLDER_B64,
    })
    assert verify.status_code == 200, verify.text

    with _db_session(app) as db:
        refreshed_old = db.get(PanelSession, old_session_id)
        assert refreshed_old.revoked_at is not None
        cred = db.query(WebauthnCredential).filter_by(owner_id=OWNER_ID).one()
        assert cred.sign_count == 6


# ── passkey step-up (§10.5.8, §10.5.8.1) ────────────────────────────────────

def _login_session_cookie(app, client, monkeypatch) -> str:
    with _db_session(app) as db:
        db.add(WebauthnCredential(owner_id=OWNER_ID, credential_id=b"step-cred",
                                  public_key=b"step-pub", sign_count=0))
        db.commit()
    _do_telegram_login(client)
    client.post("/auth/passkey/login/options")
    monkeypatch.setattr(webauthn, "verify_authentication_response", lambda **kwargs: VerifiedAuthentication(
        credential_id=b"step-cred", new_sign_count=1,
        credential_device_type="single_device", credential_backed_up=False, user_verified=True,
    ))
    client.post("/auth/passkey/login/verify", json={
        "credential_id": STEP_CRED_B64, "client_data": PLACEHOLDER_B64,
        "authenticator_data": PLACEHOLDER_B64, "signature": PLACEHOLDER_B64,
    })
    return client.cookies["helm_panel_session"]


def test_assert_options_requires_session(client):
    r = client.post("/auth/passkey/assert/options",
                    json={"approval_ids": ["1"], "action_hashes": ["h"]})
    assert r.status_code == 401


def test_assert_verify_rejects_challenge_from_other_session(app, client, monkeypatch):
    _login_session_cookie(app, client, monkeypatch)
    options = client.post("/auth/passkey/assert/options",
                          json={"approval_ids": ["1"], "action_hashes": ["h"]})
    challenge_id = options.json()["challenge_id"]

    with _db_session(app) as db:
        foreign = PanelSession(owner_id=OWNER_ID, expires_at=utcnow() + timedelta(hours=1))
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id

    other_client = TestClient(app, base_url="https://testserver")
    other_client.cookies.set("helm_panel_session", str(foreign_id))
    monkeypatch.setattr(webauthn, "verify_authentication_response", lambda **kwargs: VerifiedAuthentication(
        credential_id=b"step-cred", new_sign_count=2,
        credential_device_type="single_device", credential_backed_up=False, user_verified=True,
    ))
    r = other_client.post("/auth/passkey/assert/verify", json={
        "challenge_id": challenge_id, "credential_id": STEP_CRED_B64,
        "client_data": PLACEHOLDER_B64, "authenticator_data": PLACEHOLDER_B64, "signature": PLACEHOLDER_B64,
    })
    assert r.status_code == 401


def test_assert_verify_does_not_consume_challenge(app, client, monkeypatch):
    """require_stepup (deps.py) потребляет challenge при самой записи, не verify()."""
    _login_session_cookie(app, client, monkeypatch)
    options = client.post("/auth/passkey/assert/options",
                          json={"approval_ids": ["1"], "action_hashes": ["h"]})
    challenge_id = options.json()["challenge_id"]

    monkeypatch.setattr(webauthn, "verify_authentication_response", lambda **kwargs: VerifiedAuthentication(
        credential_id=b"step-cred", new_sign_count=2,
        credential_device_type="single_device", credential_backed_up=False, user_verified=True,
    ))
    first = client.post("/auth/passkey/assert/verify", json={
        "challenge_id": challenge_id, "credential_id": STEP_CRED_B64,
        "client_data": PLACEHOLDER_B64, "authenticator_data": PLACEHOLDER_B64, "signature": PLACEHOLDER_B64,
    })
    assert first.status_code == 200
    second = client.post("/auth/passkey/assert/verify", json={
        "challenge_id": challenge_id, "credential_id": STEP_CRED_B64,
        "client_data": PLACEHOLDER_B64, "authenticator_data": PLACEHOLDER_B64, "signature": PLACEHOLDER_B64,
    })
    assert second.status_code == 200  # verify — не точка потребления
