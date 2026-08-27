"""Жизненный цикл одобрения и исполнение действия (ТЗ §8.4).

Единственный путь исполнения любого действия проходит здесь. Это не стиль,
а условие §30.12 «RED bypass = 0»: если бы executor можно было вызвать
напрямую из другого модуля, проверка уровня стала бы договорённостью, а не
свойством системы.

Порядок §8.4 соблюдается буквально:

    предложение → канонизация → хранение payload/хэша → запрос владельцу
    → решение → сверка хэша, личности и TTL → перепроверка preconditions
    → ровно то действие → audit
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..actions.policy import Level
from ..actions.registry import ActionRegistry, PreconditionFailed
from ..models import Approval, ApprovalStatus, Task, TaskEvent, utcnow


class ApprovalError(RuntimeError):
    """Одобрение нельзя применить в текущем виде."""


class NotAuthorized(ApprovalError):
    """Решение принято не владельцем (§30.2)."""


class ApprovalExpired(ApprovalError):
    """TTL истёк (§8.4)."""


class ActionHashMismatch(ApprovalError):
    """Payload изменился после одобрения — подмена параметров (§8.4)."""


class AlreadyExecuted(ApprovalError):
    """Действие уже исполнено; повтор не создаёт второго эффекта."""


@dataclass
class ExecCtx:
    """Контекст исполнения. Значений секретов не содержит (§8.3)."""

    approval_id: str | None
    task_id: str | None
    idempotency_key: str


def _short_id() -> str:
    """Короткий идентификатор для `/helm_approve <short-id>` (§8.5)."""
    return secrets.token_hex(3)


def _idempotency_key(action_type: str, action_hash: str, task_id: uuid.UUID | None) -> str:
    """Ключ ровно-однократности.

    Задача входит в ключ намеренно: одно и то же действие с одним payload,
    предложенное дважды в рамках разных задач, — это два разных намерения
    владельца. А внутри одной задачи повторное предложение того же действия
    должно попасть в уже существующее одобрение, а не завести второе.
    """
    material = f"{action_type}\x00{action_hash}\x00{task_id or ''}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ApprovalService:
    def __init__(self, session: Session, registry: ActionRegistry, owner_id: str):
        self.session = session
        self.registry = registry
        #: Единственный владелец системы (§7.2 owner identity). Любое решение
        #: от другого identity отвергается, каким бы каналом оно ни пришло.
        self.owner_id = owner_id

    # ── audit ───────────────────────────────────────────────────────────────

    def _audit(self, task_id: uuid.UUID | None, actor: str, event_type: str,
               payload: dict[str, Any] | None = None) -> None:
        if task_id is None:
            return
        self.session.add(
            TaskEvent(task_id=task_id, actor=actor, event_type=event_type,
                      payload_redacted=payload)
        )

    # ── предложение ─────────────────────────────────────────────────────────

    def propose(self, action_type: str, payload: dict[str, Any], *,
                task_id: uuid.UUID | None = None, proposed_by: str = "hermes") -> Approval:
        """Предложить действие. Возвращает существующее одобрение при повторе.

        Уровень берётся из policy и не может быть передан вызывающим:
        предложение приходит от Hermes, а Hermes не имеет права назначать
        уровень (§8.2).
        """
        registered = self.registry.get(action_type)
        spec = self.registry.policy_for(action_type)
        canonical = registered.canonical_payload(payload)
        act_hash = registered.hash_of(payload)
        key = _idempotency_key(action_type, act_hash, task_id)

        existing = self.session.scalar(select(Approval).where(Approval.idempotency_key == key))
        if existing is not None:
            return existing

        approval = Approval(
            task_id=task_id,
            action_type=action_type,
            action_hash=act_hash,
            payload_encrypted_or_reference=canonical,
            expires_at=utcnow() + spec.approval_ttl,
            status=ApprovalStatus.PENDING,
            idempotency_key=key,
            short_id=_short_id(),
        )
        self.session.add(approval)
        try:
            self.session.flush()
        except IntegrityError:
            # Гонка двух предложений одного действия: побеждает первое.
            self.session.rollback()
            found = self.session.scalar(select(Approval).where(Approval.idempotency_key == key))
            if found is None:
                raise
            return found

        self._audit(task_id, proposed_by, "action.proposed",
                    {"action_type": action_type, "action_hash": act_hash,
                     "level": spec.initial_level.name})
        return approval

    # ── решение владельца ───────────────────────────────────────────────────

    def decide(self, approval_id: uuid.UUID, *, approve: bool, decided_by: str,
               channel: str, now: datetime | None = None) -> Approval:
        now = now or utcnow()
        approval = self.session.get(Approval, approval_id)
        if approval is None:
            raise ApprovalError(f"одобрение {approval_id} не найдено")

        if decided_by != self.owner_id:
            self._audit(approval.task_id, decided_by, "approval.unauthorized",
                        {"approval_id": str(approval_id), "channel": channel})
            raise NotAuthorized(
                f"решение принято identity {decided_by!r}, владелец — {self.owner_id!r}"
            )

        if approval.status != ApprovalStatus.PENDING:
            raise ApprovalError(
                f"одобрение уже в статусе {approval.status}; повторное решение не принимается"
            )

        if approval.expires_at <= now:
            approval.status = ApprovalStatus.EXPIRED
            self._audit(approval.task_id, "system", "approval.expired",
                        {"approval_id": str(approval_id)})
            raise ApprovalExpired(f"TTL истёк {approval.expires_at.isoformat()}")

        approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        approval.decided_at = now
        approval.decided_by = decided_by
        approval.channel = channel
        self._audit(approval.task_id, decided_by,
                    "approval.approved" if approve else "approval.rejected",
                    {"approval_id": str(approval_id), "channel": channel})
        return approval

    # ── исполнение ──────────────────────────────────────────────────────────

    def execute_approved(self, approval_id: uuid.UUID, *,
                         current_payload: dict[str, Any] | None = None,
                         now: datetime | None = None) -> Any:
        """Исполнить одобренное действие ровно один раз.

        `current_payload` — то, что исполнитель собирается отправить сейчас.
        Он сверяется с хэшем, зафиксированным при одобрении: именно здесь
        ловится подмена параметров между «владелец одобрил» и «система
        исполняет» (§8.4, §30.2 «action hash mismatch rejected»).
        """
        now = now or utcnow()
        approval = self.session.get(Approval, approval_id)
        if approval is None:
            raise ApprovalError(f"одобрение {approval_id} не найдено")

        if approval.status in (ApprovalStatus.EXECUTED, ApprovalStatus.EXECUTING):
            raise AlreadyExecuted(f"одобрение {approval_id} уже исполнено")
        if approval.status != ApprovalStatus.APPROVED:
            raise ApprovalError(
                f"исполнение запрещено: статус {approval.status}, требуется APPROVED"
            )
        if approval.expires_at <= now:
            approval.status = ApprovalStatus.EXPIRED
            raise ApprovalExpired(f"TTL истёк {approval.expires_at.isoformat()}")

        registered = self.registry.get(approval.action_type)
        payload = current_payload if current_payload is not None else approval.payload_encrypted_or_reference
        if registered.hash_of(payload) != approval.action_hash:
            self._audit(approval.task_id, "system", "approval.hash_mismatch",
                        {"approval_id": str(approval_id), "expected": approval.action_hash})
            raise ActionHashMismatch(
                "payload отличается от одобренного — исполнение отменено"
            )

        ctx = ExecCtx(approval_id=str(approval.id),
                      task_id=str(approval.task_id) if approval.task_id else None,
                      idempotency_key=approval.idempotency_key)

        # Перепроверка предусловий непосредственно перед действием (§8.4):
        # между одобрением и этим моментом могло пройти 24 часа.
        try:
            self.registry.check_preconditions(approval.action_type, payload, ctx)
        except PreconditionFailed as exc:
            approval.status = ApprovalStatus.FAILED
            self._audit(approval.task_id, "system", "approval.precondition_failed",
                        {"approval_id": str(approval_id), "precondition": exc.name})
            raise

        # Атомарный claim: APPROVED → EXECUTING. Второй исполнитель, дошедший
        # сюда одновременно, получит rowcount 0 и не вызовет executor.
        claimed = self.session.execute(
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ApprovalStatus.APPROVED)
            .values(status=ApprovalStatus.EXECUTING)
        )
        if claimed.rowcount != 1:
            raise AlreadyExecuted(f"одобрение {approval_id} уже захвачено другим исполнителем")

        try:
            result = registered.executor(registered.parse(payload), ctx)
        except Exception as exc:
            approval.status = ApprovalStatus.FAILED
            self._audit(approval.task_id, "system", "action.failed",
                        {"approval_id": str(approval_id), "error": type(exc).__name__})
            raise

        approval.status = ApprovalStatus.EXECUTED
        approval.executed_at = now
        self._audit(approval.task_id, "system", "action.executed",
                    {"approval_id": str(approval_id), "action_type": approval.action_type})
        return result

    # ── прямое исполнение GREEN/YELLOW ──────────────────────────────────────

    def execute_direct(self, action_type: str, payload: dict[str, Any], *,
                       task_id: uuid.UUID | None = None,
                       effective_level: Level | None = None) -> Any:
        """Исполнить действие без одобрения — только если оно не RED.

        `effective_level` позволяет graduated trust (§8.7) понизить уровень
        действия, но не ниже minimum_allowed_level: проверку делает policy,
        а не вызывающий.
        """
        spec = self.registry.policy_for(action_type)
        level = spec.initial_level
        if effective_level is not None:
            spec.check_demotion(effective_level)
            level = effective_level

        if level >= Level.RED:
            self._audit(task_id, "system", "action.blocked_red",
                        {"action_type": action_type})
            raise ApprovalError(
                f"{action_type}: уровень RED — исполнение без действующего одобрения "
                f"невозможно (§8.1)"
            )

        registered = self.registry.get(action_type)
        act_hash = registered.hash_of(payload)
        ctx = ExecCtx(approval_id=None, task_id=str(task_id) if task_id else None,
                      idempotency_key=_idempotency_key(action_type, act_hash, task_id))
        self.registry.check_preconditions(action_type, payload, ctx)
        result = registered.executor(registered.parse(payload), ctx)
        self._audit(task_id, "system", "action.executed_direct",
                    {"action_type": action_type, "level": level.name})
        return result
