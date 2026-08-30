"""Зависимости FastAPI: сессия БД, сессия панели, step-up (ТЗ §10.5.6–10.5.8.1)."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..knowledge.tenancy import knowledge_principal, parse_knowledge_principal  # noqa: F401
from ..models import Approval, PanelSession, PanelStepUpChallenge, utcnow

SESSION_COOKIE = "helm_panel_session"


def get_session(request: Request) -> Session:
    with request.app.state.session_factory() as session:
        yield session


@dataclass
class PanelIdentity:
    owner_id: str
    session_id: uuid.UUID
    #: v3.8 P8.6.5: заполнено, только если сессия принадлежит
    #: KNOWLEDGE_USER. `None` = сессия владельца.
    knowledge_user_id: uuid.UUID | None = None


def require_panel_session(
    request: Request,
    helm_panel_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> PanelIdentity:
    """Действующая сессия панели.

    §10.5.6: 24 часа, один активный device. Отозванная сессия отвергается
    сразу — новая успешная связка Telegram+passkey отзывает предыдущую, и
    старое устройство не должно продолжать читать.
    """
    if not helm_panel_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "нет сессии")

    with request.app.state.session_factory() as db:
        try:
            session_id = uuid.UUID(helm_panel_session)
        except ValueError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "некорректная сессия")

        record = db.get(PanelSession, session_id)
        now = utcnow()
        if record is None or record.revoked_at is not None or record.expires_at <= now:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "сессия недействительна")
        record.last_seen_at = now
        db.commit()
        return PanelIdentity(owner_id=record.owner_id, session_id=record.id,
                             knowledge_user_id=parse_knowledge_principal(record.owner_id))


def require_owner_session(
    identity: PanelIdentity = Depends(require_panel_session),
) -> PanelIdentity:
    """Разделы владельца (§14.3 "KNOWLEDGE_USER: Knowledge-only panel shell").

    KNOWLEDGE_USER не должен видеть ни одобрения, ни задачи, ни деньги, ни
    систему, ни список других пользователей — и не «по недосмотру фронта»,
    а отказом на сервере. Проверка одна и в одном месте: сессия с
    принципалом `ku:` не проходит дальше.
    """
    if identity.knowledge_user_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "раздел доступен только владельцу")
    return identity


@dataclass
class StepUp:
    """Потреблённая passkey-церемония, привязанная к конкретным действиям."""

    challenge_id: uuid.UUID
    action_hashes: list[str]
    approval_ids: list[str]

    def assert_binds(self, approval_id: uuid.UUID, db: Session) -> None:
        """Проверить, что церемония подписывала ИМЕННО это действие.

        §10.5.8.1: «assertion, выданная для одного действия, не может одобрить
        другое». Сверяются две вещи: идентификатор одобрения входил в набор
        церемонии И хэш действия с тех пор не изменился. Первого мало —
        payload мог быть переписан между подписью и вызовом.
        """
        if str(approval_id) not in self.approval_ids:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "passkey-подтверждение выдано для другого действия",
            )
        approval = db.get(Approval, approval_id)
        if approval is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "одобрение не найдено")
        if not any(hmac.compare_digest(approval.action_hash, h) for h in self.action_hashes):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "действие изменилось после подтверждения — подпись недействительна",
            )

    def assert_scope(self, scope: str) -> None:
        """То же правило §10.5.8.1 для действий без одобрения (v3.8 P8.6.5,
        раздел «Пользователи»): церемония годится только для той операции,
        под которую её запрашивали.

        Без этого подтверждение, полученное на «пригласить», подошло бы к
        «приостановить», а полученное на приостановку пользователя A — к
        приостановке B: scope включает идентификатор цели, не только имя
        операции.
        """
        if not any(hmac.compare_digest(scope, h) for h in self.action_hashes):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "passkey-подтверждение выдано для другой операции",
            )


def require_stepup(
    request: Request,
    identity: PanelIdentity = Depends(require_panel_session),
    x_helm_stepup: str | None = Header(default=None),
) -> StepUp:
    """Свежее passkey-подтверждение на каждую запись (§10.5.8).

    Никакого «запомнить на 30 дней»: challenge живёт 60 секунд и потребляется
    ровно один раз. Повторное использование того же подтверждения — отказ,
    иначе перехваченная assertion работала бы до конца суток.
    """
    if not x_helm_stepup:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "требуется passkey-подтверждение")

    with request.app.state.session_factory() as db:
        try:
            challenge_id = uuid.UUID(x_helm_stepup)
        except ValueError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "некорректное подтверждение")

        record = db.get(PanelStepUpChallenge, challenge_id)
        now = utcnow()
        if record is None or record.session_id != identity.session_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "подтверждение не найдено")
        if record.used_at is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "подтверждение уже использовано")
        if record.expires_at <= now:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "подтверждение истекло")

        record.used_at = now  # потребляется ровно один раз
        db.commit()
        return StepUp(challenge_id=record.id,
                      action_hashes=list(record.action_hashes or []),
                      approval_ids=[str(a) for a in (record.approval_ids or [])])
