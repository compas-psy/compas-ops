"""v3.8 §9.0/§14.3, P8.6.2 — onboarding нового `KNOWLEDGE_USER`.

Инвайт создаётся ДО того, как секьюрити-принадлежность вообще известна
(владелец знает только "кого хочет пригласить", не Telegram `from.id`
до первого приватного контакта с Dedicated Knowledge Bot) — тот же
двухфазный принцип, что уже есть у `PanelEnrollmentToken` (`api/auth.py`):
хэш токена в БД, сам токен — только в момент создания, единственный раз.

`create_invite()`/`consume_invite()` НЕ знают о конкретном канале
доставки (Telegram vs что-то другое): `channel`/`external_user_id`
передаются вызывающей стороной. `knowledge_users`/`knowledge_invites`/
`knowledge_channel_identities` — реестр тенантов, НЕ tenant-scoped
Knowledge-контент — RLS на них сознательно не распространяется (см.
`helm_core/knowledge/rls.py`, `V3.8-DELTA.md`), поэтому здесь нет
`bind_knowledge_user()`.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .tenancy import knowledge_principal
from ..models import (
    KnowledgeChannelIdentity, KnowledgeInvite, KnowledgeUser, KnowledgeUserRole, KnowledgeUserStatus,
    PanelEnrollmentToken, PanelSession, WebauthnCredential,
)
from ..models.base import utcnow

#: §14.3: "short expiry" — 24 часа, тот же порядок, что и у voice-спула
#: (§14.10), не бессрочная ссылка, гуляющая по переписке.
DEFAULT_INVITE_TTL = timedelta(hours=24)


@dataclass
class CreateInviteResult:
    user: KnowledgeUser
    invite: KnowledgeInvite
    #: Единственный момент, когда сырой токен вообще существует за
    #: пределами сообщения владельцу — БД хранит только token_hash.
    raw_token: str


def create_invite(session: Session, *, created_by: str, display_name: str | None = None,
                  timezone: str = "Europe/Moscow", locale: str = "ru",
                  storage_quota_bytes: int | None = None,
                  daily_ingest_quota_bytes: int | None = None,
                  expected_external_user_id: str | None = None,
                  ttl: timedelta = DEFAULT_INVITE_TTL) -> CreateInviteResult:
    """Завести нового `KNOWLEDGE_USER` в статусе `INVITED` + одноразовый
    токен. Пользователь становится `ACTIVE` только после `consume_invite()`
    — сама эта функция доступа никому не даёт (§14.3: "SYSTEM_OWNER
    creates knowledge_user status=INVITED").
    """
    user = KnowledgeUser(
        role=KnowledgeUserRole.KNOWLEDGE_USER, status=KnowledgeUserStatus.INVITED,
        display_name=display_name, locale=locale, timezone=timezone,
        storage_quota_bytes=storage_quota_bytes,
        daily_ingest_quota_bytes=daily_ingest_quota_bytes,
    )
    session.add(user)
    session.flush()

    raw_token = secrets.token_urlsafe(32)
    invite = KnowledgeInvite(
        knowledge_user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expected_external_user_id=expected_external_user_id,
        created_by=created_by, expires_at=utcnow() + ttl,
    )
    session.add(invite)
    session.flush()
    return CreateInviteResult(user=user, invite=invite, raw_token=raw_token)


@dataclass
class ConsumeInviteOutcome:
    status: Literal["invalid", "expired", "used", "revoked", "id_mismatch",
                    "identity_already_bound", "success"]
    user: KnowledgeUser | None = None


def consume_invite(session: Session, *, raw_token: str, channel: str, external_user_id: str,
                   external_chat_id: str | None = None) -> ConsumeInviteOutcome:
    """Первый приватный контакт с Dedicated Knowledge Bot (§14.3 шаги 1-9).

    Порядок проверок — от дешёвой к дорогой, но принципиален только для
    `expired`/`used`/`revoked` до `id_mismatch`: "истёк"/"уже
    использован" — состояние самого токена, не зависит от того, кто его
    предъявил; "не тот Telegram id" — проверка личности предъявителя,
    имеет смысл только для токена, который сам по себе ещё годен.
    """
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    invite = session.scalar(select(KnowledgeInvite).where(KnowledgeInvite.token_hash == token_hash))
    if invite is None:
        return ConsumeInviteOutcome(status="invalid")
    if invite.revoked_at is not None:
        return ConsumeInviteOutcome(status="revoked")
    if invite.used_at is not None:
        return ConsumeInviteOutcome(status="used")
    if invite.expires_at <= utcnow():
        return ConsumeInviteOutcome(status="expired")
    if (invite.expected_external_user_id is not None
            and invite.expected_external_user_id != external_user_id):
        return ConsumeInviteOutcome(status="id_mismatch")

    # §14.3 "No second user can claim the same active Telegram identity" —
    # проверка ДО записи: тот же физический Telegram-аккаунт не может
    # разом стать identity двух разных knowledge_user.
    existing_identity = session.scalar(
        select(KnowledgeChannelIdentity).where(
            KnowledgeChannelIdentity.channel == channel,
            KnowledgeChannelIdentity.external_user_id == external_user_id,
            KnowledgeChannelIdentity.revoked_at.is_(None),
        )
    )
    if existing_identity is not None:
        return ConsumeInviteOutcome(status="identity_already_bound")

    user = session.get(KnowledgeUser, invite.knowledge_user_id)

    identity = KnowledgeChannelIdentity(
        knowledge_user_id=user.id, channel=channel, external_user_id=external_user_id,
        external_chat_id=external_chat_id, is_primary=True,
    )
    session.add(identity)

    invite.used_at = utcnow()
    if user.status != KnowledgeUserStatus.ACTIVE:
        user.status = KnowledgeUserStatus.ACTIVE
        user.activated_at = utcnow()
    session.flush()

    return ConsumeInviteOutcome(status="success", user=user)


def find_user_by_identity(session: Session, *, channel: str,
                          external_user_id: str) -> KnowledgeUser | None:
    """Без фильтра по статусу — ТОЛЬКО для UX-сообщения (различить
    "приостановлено" от "нет доступа вовсе"). Решение о доступе всегда
    принимает `resolve_active_user_by_identity()`, никогда эта функция."""
    identity = session.scalar(
        select(KnowledgeChannelIdentity).where(
            KnowledgeChannelIdentity.channel == channel,
            KnowledgeChannelIdentity.external_user_id == external_user_id,
            KnowledgeChannelIdentity.revoked_at.is_(None),
        )
    )
    if identity is None:
        return None
    return session.get(KnowledgeUser, identity.knowledge_user_id)


@dataclass
class UserActionOutcome:
    status: Literal["not_found", "success", "noop"]
    user: KnowledgeUser | None = None


def revoke_panel_access(session: Session, knowledge_user_id: uuid.UUID) -> int:
    """Отозвать действующие сессии панели тенанта и неиспользованные
    enrollment-токены. Возвращает число отозванных сессий.

    §14.3 "SUSPENDED: bot rejects Knowledge operations, **panel sessions
    revoked**". До появления входа KNOWLEDGE_USER в панель (P8.6.5)
    отзывать было нечего, и `suspend_user()` этого не делал; теперь
    сессия живёт до суток, и не отозвать её значит оставить
    приостановленному человеку сутки чтения.

    Неиспользованные enrollment-токены гасятся тем же движением: токен,
    выданный до приостановки, не должен превращаться в новый вход после
    неё.
    """
    principal = knowledge_principal(knowledge_user_id)
    now = utcnow()
    revoked = 0
    for panel_session in session.scalars(
        select(PanelSession).where(PanelSession.owner_id == principal,
                                   PanelSession.revoked_at.is_(None))
    ):
        panel_session.revoked_at = now
        revoked += 1
    for token in session.scalars(
        select(PanelEnrollmentToken).where(PanelEnrollmentToken.owner_id == principal,
                                           PanelEnrollmentToken.used_at.is_(None))
    ):
        token.used_at = now
    session.flush()
    return revoked


def reset_panel_passkey(session: Session, knowledge_user_id: uuid.UUID) -> UserActionOutcome:
    """§14.3 "SYSTEM_OWNER can reset a secondary user's passkey
    enrollment; old sessions are revoked".

    Сам сброс — отзыв всех credential'ов: passkey нельзя «перевыпустить»,
    приватный ключ живёт на устройстве человека и серверу неизвестен.
    После сброса вход возможен только через новый enrollment-токен, то
    есть через явное действие владельца — что и есть смысл сброса при
    потерянном устройстве.
    """
    user = session.get(KnowledgeUser, knowledge_user_id)
    if user is None:
        return UserActionOutcome(status="not_found")

    principal = knowledge_principal(knowledge_user_id)
    now = utcnow()
    reset_any = False
    for credential in session.scalars(
        select(WebauthnCredential).where(WebauthnCredential.owner_id == principal,
                                         WebauthnCredential.revoked_at.is_(None))
    ):
        credential.revoked_at = now
        reset_any = True
    revoke_panel_access(session, knowledge_user_id)
    session.flush()
    return UserActionOutcome(status="success" if reset_any else "noop", user=user)


def suspend_user(session: Session, knowledge_user_id: uuid.UUID) -> UserActionOutcome:
    """§14.3 "Suspend/offboard": bot/panel access blocked, data retained.
    Идемпотентно — повторный suspend уже suspended-пользователя не
    ошибка (`noop`), не исключение."""
    user = session.get(KnowledgeUser, knowledge_user_id)
    if user is None:
        return UserActionOutcome(status="not_found")
    if user.status == KnowledgeUserStatus.SUSPENDED:
        # Идемпотентность не отменяет отзыва: сессия могла появиться
        # между двумя вызовами.
        revoke_panel_access(session, knowledge_user_id)
        return UserActionOutcome(status="noop", user=user)
    user.status = KnowledgeUserStatus.SUSPENDED
    user.suspended_at = utcnow()
    revoke_panel_access(session, knowledge_user_id)
    session.flush()
    return UserActionOutcome(status="success", user=user)


def reactivate_user(session: Session, knowledge_user_id: uuid.UUID) -> UserActionOutcome:
    """Обратное `suspend_user()` — НЕ трогает `DELETED` (RED-действие,
    отдельное, необратимое; reactivate из DELETED был бы тихим обходом
    того, что должно требовать явного отдельного решения)."""
    user = session.get(KnowledgeUser, knowledge_user_id)
    if user is None:
        return UserActionOutcome(status="not_found")
    if user.status == KnowledgeUserStatus.DELETED:
        return UserActionOutcome(status="noop", user=user)
    if user.status == KnowledgeUserStatus.ACTIVE:
        return UserActionOutcome(status="noop", user=user)
    user.status = KnowledgeUserStatus.ACTIVE
    user.suspended_at = None
    session.flush()
    return UserActionOutcome(status="success", user=user)


def resolve_active_user_by_identity(session: Session, *, channel: str,
                                    external_user_id: str) -> KnowledgeUser | None:
    """Разрешить уже verified identity в `KnowledgeUser`, ТОЛЬКО если тот
    `ACTIVE` (§14.3 "Unknown user without valid invite gets no Knowledge
    access"; §14.3 "SUSPENDED: bot rejects Knowledge operations") — вызов
    ошибочно определяющий suspended/deleted как валидный доступ был бы
    именно тем, что спека прямо запрещает."""
    user = find_user_by_identity(session, channel=channel, external_user_id=external_user_id)
    if user is None or user.status != KnowledgeUserStatus.ACTIVE:
        return None
    return user
