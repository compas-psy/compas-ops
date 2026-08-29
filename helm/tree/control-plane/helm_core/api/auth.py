"""Аутентификация панели (ТЗ §10.5.6-§10.5.8.2).

Три отдельных, но связанных потока:

1. Telegram Login Widget (§10.5.6) — подтверждает, что за браузером сидит
   владелец. Спека называет предпочтительным способом OIDC (Authorization
   Code+PKCE), но для этого бота он у BotFather недоступен (проверено
   вживую 29.08.2026: "Web login is currently unavailable", а после
   привязки домена открывается документация только classic-виджета) —
   используется официальный, но более старый механизм Telegram с проверкой
   HMAC-подписи на самом токене бота.
2. Первый enrollment passkey (§10.5.7) — единственный путь получить первый
   WebauthnCredential, когда его ещё ни одного нет.
3. Passkey-логин (после первого enrollment) и passkey step-up на запись
   (§10.5.8/§10.5.8.1) — сюда же входит серверная часть /auth/passkey/assert/*,
   чей JSON-контракт уже зафиксирован фронтендом (panel/src/components/passkey.ts),
   написанным раньше этого модуля.

Между подтверждением Telegram и готовой PanelSession нет отдельной таблицы —
переходное состояние "Telegram подтвердил, passkey ещё нет" живёт в подписанной
cookie на 10 минут, а не в БД: это состояние одного диалога в одном браузере,
а не то, что должно пережить рестарт сервера.

`require_stepup` (deps.py, уже существующий и протестированный код) потребляет
challenge ровно один раз — при самой записи, по заголовку X-Helm-StepUp. Этот
модуль в /auth/passkey/assert/verify НЕ трогает used_at: его роль — проверить
подлинность passkey-assertion и вернуть ok фронтенду, а не решать, состоялась
ли запись. Свежесть и однократность гарантирует короткий TTL challenge (60с,
settings.stepup_challenge_ttl_seconds) плюс привязка к session_id, а не двойное
потребление одного и того же поля.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import time
import uuid
from datetime import timedelta
from typing import Any

import webauthn
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticationCredential,
    AuthenticatorAssertionResponse,
    AuthenticatorAttestationResponse,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialType,
    RegistrationCredential,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..config import Settings
from ..models import PanelEnrollmentToken, PanelSession, PanelStepUpChallenge, WebauthnCredential, utcnow
from .deps import SESSION_COOKIE, PanelIdentity, get_session, require_panel_session

router = APIRouter(prefix="/auth", tags=["auth"])

PENDING_COOKIE = "helm_auth_pending"
PENDING_TTL_SECONDS = 600


# ── кодирование ──────────────────────────────────────────────────────────────

def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _from_b64u(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "некорректная base64url-строка") from exc


# ── подписанная переходная cookie (OIDC state → passkey ceremony) ───────────

def _pack(secret: str, payload: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    sig = hmac_mod.new(secret.encode(), body, hashlib.sha256).digest()
    return f"{body.decode()}.{_b64u(sig)}"


def _unpack(secret: str, token: str) -> dict[str, Any] | None:
    try:
        body_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64u(hmac_mod.new(secret.encode(), body_b64.encode(), hashlib.sha256).digest())
    # .encode(): cookie приходит от клиента — не-ASCII в ней роняет
    # compare_digest TypeError'ом вместо того, чтобы честно не совпасть.
    if not hmac_mod.compare_digest(sig_b64.encode(), expected.encode()):
        return None
    padded = body_b64 + "=" * (-len(body_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def _set_pending(response: Response, request: Request, payload: dict[str, Any]) -> None:
    secret: str = request.app.state.panel_auth_cookie_secret
    response.set_cookie(PENDING_COOKIE, _pack(secret, payload), httponly=True,
                        secure=True, samesite="lax", max_age=PENDING_TTL_SECONDS, path="/auth")


def _read_pending(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(PENDING_COOKIE)
    if not token:
        return None
    secret: str = request.app.state.panel_auth_cookie_secret
    return _unpack(secret, token)


def _require_pending(request: Request, *, purpose: str) -> dict[str, Any]:
    pending = _read_pending(request)
    if pending is None or pending.get("purpose") != purpose:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "начните вход заново")
    return pending


def _clear_pending(response: Response) -> None:
    response.delete_cookie(PENDING_COOKIE, path="/auth")


def _set_session_cookie(response: Response, settings: Settings, panel_session: PanelSession) -> None:
    response.set_cookie(SESSION_COOKIE, str(panel_session.id), httponly=True, secure=True,
                        samesite="lax", max_age=settings.panel_session_ttl_hours * 3600, path="/")


def _create_session(session: Session, settings: Settings, owner_id: str) -> PanelSession:
    """Новый успешный вход отзывает предыдущие сессии (§10.5.6: одно активное устройство)."""
    now = utcnow()
    for existing in session.scalars(
        select(PanelSession).where(PanelSession.owner_id == owner_id, PanelSession.revoked_at.is_(None))
    ):
        existing.revoked_at = now
    record = PanelSession(owner_id=owner_id, expires_at=now + timedelta(hours=settings.panel_session_ttl_hours))
    session.add(record)
    session.flush()
    return record


# ── Telegram Login Widget (§10.5.6) ─────────────────────────────────────────
# OIDC (Authorization Code+PKCE), который спека называет предпочтительным
# способом, у BotFather для этого бота недоступен ("Web login is currently
# unavailable" до привязки домена, а после привязки открывается только
# документация classic-виджета, без отдельного OIDC/Client ID) — проверено
# вживую 29.08.2026, не предположение. Виджет — тот же официальный механизм
# Telegram (core.telegram.org/widgets/login), просто без OIDC-обвязки:
# подлинность отправителя подтверждается HMAC-SHA256 на самом токене бота,
# а не отдельным Client Secret.

TELEGRAM_AUTH_MAX_AGE_SECONDS = 300


def verify_login_widget(bot_token: str, fields: dict[str, str]) -> dict[str, str]:
    """Проверить подпись Telegram Login Widget.

    https://core.telegram.org/widgets/login#checking-authorization —
    data_check_string собирается из всех полей, кроме hash, отсортированных
    по ключу и склеенных "key=value\\n"; secret_key = SHA256(bot_token);
    ожидаемый hash = HMAC-SHA256(secret_key, data_check_string).
    """
    received_hash = fields.get("hash", "")
    payload = {k: v for k, v in fields.items() if k != "hash"}
    data_check_string = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    expected_hash = hmac_mod.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    # .encode(): не-ASCII в присланном hash не должен ронять TypeError вместо 401.
    if not hmac_mod.compare_digest(expected_hash.encode(), received_hash.encode()):
        raise ValueError("подпись Telegram не совпадает")

    auth_date = int(payload.get("auth_date", "0"))
    if time.time() - auth_date > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        raise ValueError("данные входа устарели, начните заново")
    return payload


@router.get("/telegram/callback")
def telegram_callback(request: Request, session: Session = Depends(get_session)) -> Response:
    settings: Settings = request.app.state.settings
    fields = dict(request.query_params)
    try:
        claims = verify_login_widget(request.app.state.telegram_bot_token, fields)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Telegram не подтвердил вход: {exc}") from exc

    # Без префикса "tg:": Hermes (helm-control) шлёт в /internal/inbound
    # голый str(chat_id) (hermes/plugins/helm-control/__init__.py), и
    # settings.owner_id читается из того же секрета telegram_owner_id без
    # какой-либо нормализации (ingest.py сравнивает как есть). Виджет тоже
    # обязан сравнивать с тем же голым числом, а не изобретать свой формат.
    owner_id = str(claims.get("id"))
    if owner_id != settings.owner_id:
        # Не создаём вообще никакого состояния для не-владельца — ни сессии,
        # ни pending-cookie на попытку enrollment.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "вход доступен только владельцу")

    has_credential = session.scalar(
        select(WebauthnCredential.id).where(WebauthnCredential.owner_id == owner_id,
                                            WebauthnCredential.revoked_at.is_(None))
    ) is not None
    next_step = "login" if has_credential else "enroll"

    response = RedirectResponse(f"/login?step={next_step}", status_code=status.HTTP_302_FOUND)
    _set_pending(response, request, {
        "purpose": next_step, "owner_id": owner_id, "exp": time.time() + PENDING_TTL_SECONDS,
    })
    return response


# ── первый enrollment (§10.5.7) ─────────────────────────────────────────────

def _check_enrollment_token(session: Session, raw_token: str, owner_id: str) -> PanelEnrollmentToken:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token = session.scalar(select(PanelEnrollmentToken).where(PanelEnrollmentToken.token_hash == token_hash))
    now = utcnow()
    if (token is None or token.owner_id != owner_id or token.used_at is not None
            or token.expires_at <= now):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "enrollment-токен недействителен")
    return token


class EnrollOptionsIn(BaseModel):
    enrollment_token: str = Field(min_length=1, max_length=256)


@router.post("/passkey/register/options")
def register_options(body: EnrollOptionsIn, request: Request,
                     session: Session = Depends(get_session)) -> Response:
    pending = _require_pending(request, purpose="enroll")
    token = _check_enrollment_token(session, body.enrollment_token, pending["owner_id"])

    settings: Settings = request.app.state.settings
    options = webauthn.generate_registration_options(
        rp_id=settings.panel_rp_id, rp_name="HELM",
        user_name=pending["owner_id"], user_id=pending["owner_id"].encode(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
            resident_key=ResidentKeyRequirement.PREFERRED,
        ),
        attestation=AttestationConveyancePreference.NONE,
    )

    response = JSONResponse({
        "challenge": _b64u(options.challenge), "rp_id": options.rp.id, "rp_name": options.rp.name,
        "user_id": _b64u(options.user.id), "user_name": options.user.name,
        "timeout_ms": options.timeout,
    })
    _set_pending(response, request, {
        **pending, "challenge": _b64u(options.challenge), "enrollment_token_id": str(token.id),
        "exp": time.time() + PENDING_TTL_SECONDS,
    })
    return response


class RegisterVerifyIn(BaseModel):
    credential_id: str
    client_data: str
    attestation_object: str


@router.post("/passkey/register/verify")
def register_verify(body: RegisterVerifyIn, request: Request,
                    session: Session = Depends(get_session)) -> Response:
    pending = _require_pending(request, purpose="enroll")
    if "challenge" not in pending or "enrollment_token_id" not in pending:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "начните регистрацию заново")

    settings: Settings = request.app.state.settings
    credential = RegistrationCredential(
        id=body.credential_id, raw_id=_from_b64u(body.credential_id),
        response=AuthenticatorAttestationResponse(
            client_data_json=_from_b64u(body.client_data),
            attestation_object=_from_b64u(body.attestation_object),
        ),
        type=PublicKeyCredentialType.PUBLIC_KEY,
    )
    try:
        verification = webauthn.verify_registration_response(
            credential=credential, expected_challenge=_from_b64u(pending["challenge"]),
            expected_rp_id=settings.panel_rp_id, expected_origin=settings.panel_origin,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"passkey не подтверждён: {exc}") from exc

    owner_id = pending["owner_id"]
    token = session.get(PanelEnrollmentToken, uuid.UUID(pending["enrollment_token_id"]))
    if token is None or token.used_at is not None or token.owner_id != owner_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "enrollment-токен истёк за время церемонии")

    token.used_at = utcnow()
    session.add(WebauthnCredential(
        owner_id=owner_id, credential_id=verification.credential_id,
        public_key=verification.credential_public_key, sign_count=verification.sign_count,
    ))
    panel_session = _create_session(session, settings, owner_id)
    session.commit()

    response = JSONResponse({"status": "ok"})
    _clear_pending(response)
    _set_session_cookie(response, settings, panel_session)
    return response


# ── passkey-логин, когда credential уже есть (§10.5.7 "после первого enrollment") ──

@router.post("/passkey/login/options")
def login_options(request: Request, session: Session = Depends(get_session)) -> Response:
    pending = _require_pending(request, purpose="login")
    owner_id = pending["owner_id"]
    creds = session.scalars(
        select(WebauthnCredential).where(WebauthnCredential.owner_id == owner_id,
                                         WebauthnCredential.revoked_at.is_(None))
    ).all()
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "нет активного passkey — нужно восстановление (panel-passkey-recover)")

    settings: Settings = request.app.state.settings
    options = webauthn.generate_authentication_options(
        rp_id=settings.panel_rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=c.credential_id) for c in creds],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    response = JSONResponse({
        "challenge": _b64u(options.challenge), "rp_id": settings.panel_rp_id,
        "timeout_ms": options.timeout,
        "allow_credentials": [{"id": _b64u(c.credential_id), "type": "public-key"} for c in creds],
    })
    _set_pending(response, request, {
        **pending, "challenge": _b64u(options.challenge), "exp": time.time() + PENDING_TTL_SECONDS,
    })
    return response


class LoginVerifyIn(BaseModel):
    credential_id: str
    client_data: str
    authenticator_data: str
    signature: str


@router.post("/passkey/login/verify")
def login_verify(body: LoginVerifyIn, request: Request,
                 session: Session = Depends(get_session)) -> Response:
    pending = _require_pending(request, purpose="login")
    if "challenge" not in pending:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "начните вход заново")
    owner_id = pending["owner_id"]

    credential_id_bytes = _from_b64u(body.credential_id)
    stored = session.scalar(
        select(WebauthnCredential).where(WebauthnCredential.owner_id == owner_id,
                                         WebauthnCredential.credential_id == credential_id_bytes,
                                         WebauthnCredential.revoked_at.is_(None))
    )
    if stored is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "passkey не найден")

    settings: Settings = request.app.state.settings
    credential = AuthenticationCredential(
        id=body.credential_id, raw_id=credential_id_bytes,
        response=AuthenticatorAssertionResponse(
            client_data_json=_from_b64u(body.client_data),
            authenticator_data=_from_b64u(body.authenticator_data),
            signature=_from_b64u(body.signature), user_handle=None,
        ),
        type=PublicKeyCredentialType.PUBLIC_KEY,
    )
    try:
        verification = webauthn.verify_authentication_response(
            credential=credential, expected_challenge=_from_b64u(pending["challenge"]),
            expected_rp_id=settings.panel_rp_id, expected_origin=settings.panel_origin,
            credential_public_key=stored.public_key,
            credential_current_sign_count=stored.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"passkey не подтверждён: {exc}") from exc

    stored.sign_count = verification.new_sign_count
    stored.last_used_at = utcnow()
    panel_session = _create_session(session, settings, owner_id)
    session.commit()

    response = JSONResponse({"status": "ok"})
    _clear_pending(response)
    _set_session_cookie(response, settings, panel_session)
    return response


# ── passkey step-up на запись (§10.5.8, §10.5.8.1) ──────────────────────────
# Контракт зафиксирован фронтендом (panel/src/components/passkey.ts), написанным
# раньше этого модуля — имена полей здесь подстроены под него, не наоборот.

class AssertOptionsIn(BaseModel):
    approval_ids: list[str] = Field(min_length=1)
    action_hashes: list[str] = Field(min_length=1)


@router.post("/passkey/assert/options")
def assert_options(body: AssertOptionsIn, request: Request,
                   session: Session = Depends(get_session),
                   identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    creds = session.scalars(
        select(WebauthnCredential).where(WebauthnCredential.owner_id == identity.owner_id,
                                         WebauthnCredential.revoked_at.is_(None))
    ).all()
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "нет активного passkey")

    options = webauthn.generate_authentication_options(
        rp_id=settings.panel_rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=c.credential_id) for c in creds],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge = PanelStepUpChallenge(
        session_id=identity.session_id, action_hashes=body.action_hashes,
        approval_ids=body.approval_ids, challenge=options.challenge,
        expires_at=utcnow() + timedelta(seconds=settings.stepup_challenge_ttl_seconds),
    )
    session.add(challenge)
    session.commit()

    return {
        "challenge_id": str(challenge.id), "challenge": _b64u(options.challenge),
        "rp_id": settings.panel_rp_id, "timeout_ms": options.timeout,
        "allow_credentials": [{"id": _b64u(c.credential_id), "type": "public-key"} for c in creds],
    }


class AssertVerifyIn(BaseModel):
    challenge_id: uuid.UUID
    credential_id: str
    client_data: str
    authenticator_data: str
    signature: str


@router.post("/passkey/assert/verify")
def assert_verify(body: AssertVerifyIn, request: Request,
                  session: Session = Depends(get_session),
                  identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, str]:
    challenge = session.get(PanelStepUpChallenge, body.challenge_id)
    now = utcnow()
    if challenge is None or challenge.session_id != identity.session_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "подтверждение не найдено")
    if challenge.used_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "подтверждение уже использовано")
    if challenge.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "подтверждение истекло")

    credential_id_bytes = _from_b64u(body.credential_id)
    stored = session.scalar(
        select(WebauthnCredential).where(WebauthnCredential.owner_id == identity.owner_id,
                                         WebauthnCredential.credential_id == credential_id_bytes,
                                         WebauthnCredential.revoked_at.is_(None))
    )
    if stored is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "passkey не найден")

    settings: Settings = request.app.state.settings
    credential = AuthenticationCredential(
        id=body.credential_id, raw_id=credential_id_bytes,
        response=AuthenticatorAssertionResponse(
            client_data_json=_from_b64u(body.client_data),
            authenticator_data=_from_b64u(body.authenticator_data),
            signature=_from_b64u(body.signature), user_handle=None,
        ),
        type=PublicKeyCredentialType.PUBLIC_KEY,
    )
    try:
        verification = webauthn.verify_authentication_response(
            credential=credential, expected_challenge=bytes(challenge.challenge),
            expected_rp_id=settings.panel_rp_id, expected_origin=settings.panel_origin,
            credential_public_key=stored.public_key,
            credential_current_sign_count=stored.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"passkey не подтверждён: {exc}") from exc

    # used_at здесь намеренно не трогается — потребляет require_stepup (deps.py)
    # в момент самой записи, по X-Helm-StepUp. Здесь только доказывается, что
    # assertion подлинная, и обновляется анти-клон счётчик.
    stored.sign_count = verification.new_sign_count
    stored.last_used_at = now
    session.commit()
    return {"status": "ok"}
