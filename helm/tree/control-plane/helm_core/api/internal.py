"""Internal API (ТЗ §7.3). Всё под HMAC service auth.

Ключевой эндпоинт — `/internal/inbound`. Через него Hermes обязан
зарегистрировать входящее ДО первого обращения к модели (§9.3, A-DoD п.2).
Если Control Plane недоступен, Hermes получает ошибку и не исполняет задачу
(A-DoD п.3) — это свойство обеспечивается тем, что регистрация синхронная и
её результат нужен для продолжения, а не тем, что Hermes «должен вести себя
хорошо».
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ingest import IngestService, NotOwner
from ..models import ModelRun, Task, TaskEvent, TaskStatus, utcnow
from .deps import get_session
from .security import require_service_auth

router = APIRouter(prefix="/internal", tags=["internal"],
                   dependencies=[Depends(require_service_auth)])


class InboundMessage(BaseModel):
    channel: str = Field(min_length=1, max_length=32)
    external_message_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1)
    title_redacted: str | None = Field(default=None, max_length=280)


@router.post("/inbound", status_code=status.HTTP_201_CREATED)
def inbound(message: InboundMessage, request: Request,
            session: Session = Depends(get_session)) -> dict[str, Any]:
    """Регистрация входящего. Вызывается до любого LLM-вызова."""
    service = IngestService(session, owner_id=request.app.state.owner_id)
    try:
        result = service.register(
            channel=message.channel,
            external_message_id=message.external_message_id,
            owner_id=message.owner_id,
            text=message.text,
            title_redacted=message.title_redacted,
        )
    except NotOwner as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    session.commit()
    return {"task_id": str(result.task.id), "created": result.created,
            "dedup_reason": result.dedup_reason, "status": result.task.status}


class TaskEventIn(BaseModel):
    actor: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=64)
    payload_redacted: dict[str, Any] | None = None
    correlation_id: str | None = Field(default=None, max_length=128)


@router.post("/tasks/{task_id}/event", status_code=status.HTTP_201_CREATED)
def add_event(task_id: uuid.UUID, event: TaskEventIn,
              session: Session = Depends(get_session)) -> dict[str, str]:
    if session.get(Task, task_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "задача не найдена")
    session.add(TaskEvent(task_id=task_id, **event.model_dump()))
    session.commit()
    return {"status": "recorded"}


class TransitionIn(BaseModel):
    status: TaskStatus
    reason: str | None = Field(default=None, max_length=280)


@router.post("/tasks/{task_id}/transition")
def transition(task_id: uuid.UUID, body: TransitionIn,
               session: Session = Depends(get_session)) -> dict[str, str]:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "задача не найдена")
    previous = task.status
    task.status = body.status
    task.updated_at = utcnow()
    session.add(TaskEvent(task_id=task_id, actor="hermes", event_type="task.transition",
                          payload_redacted={"from": previous, "to": body.status,
                                            "reason": body.reason}))
    session.commit()
    return {"task_id": str(task_id), "status": task.status}


class ProposeIn(BaseModel):
    action_type: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]
    task_id: uuid.UUID | None = None


@router.post("/actions/propose", status_code=status.HTTP_201_CREATED)
def propose(body: ProposeIn, request: Request,
            session: Session = Depends(get_session)) -> dict[str, Any]:
    """Hermes предлагает действие. Уровень назначает policy, не Hermes."""
    service = request.app.state.approval_service_factory(session)
    try:
        approval = service.propose(body.action_type, body.payload, task_id=body.task_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    spec = service.registry.policy_for(body.action_type)
    session.commit()
    return {"approval_id": str(approval.id), "short_id": approval.short_id,
            "action_hash": approval.action_hash, "level": spec.initial_level.name,
            "expires_at": approval.expires_at.isoformat(), "status": approval.status}


class DecisionIn(BaseModel):
    approve: bool
    decided_by: str = Field(min_length=1, max_length=64)
    channel: str = Field(min_length=1, max_length=16)


@router.post("/approvals/{approval_id}/decision")
def decision(approval_id: uuid.UUID, body: DecisionIn, request: Request,
             session: Session = Depends(get_session)) -> dict[str, Any]:
    """Решение из Telegram (§8.5). Команда не проходит через LLM."""
    service = request.app.state.approval_service_factory(session)
    from ..actions.registry import PreconditionFailed
    from ..approvals.service import ApprovalError, NotAuthorized

    try:
        approval = service.decide(approval_id, approve=body.approve,
                                  decided_by=body.decided_by, channel=body.channel)
        result = None
        if body.approve:
            result = service.execute_approved(approval.id)
    except NotAuthorized as exc:
        session.commit()  # запись о неавторизованной попытке обязана сохраниться
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    except PreconditionFailed as exc:
        # НАЙДЕНО на живом смоук-тесте A-DoD п.4-6: без этого перехвата
        # непройденное предусловие (§8.4 — перепроверяется прямо перед
        # исполнением, между approve и этим моментом могло пройти 24ч)
        # улетало как голый 500 Internal Server Error вместо осмысленной
        # ошибки. status уже FAILED — execute_approved() выставляет его
        # сам до re-raise.
        session.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except ApprovalError as exc:
        session.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    session.commit()
    return {"approval_id": str(approval_id), "status": approval.status, "result": result}


class ModelRunIn(BaseModel):
    profile: str | None = None
    alias: str | None = None
    concrete_model: str | None = None
    provider: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    #: Строкой, не float: стоимость проходит в отчёты и в панель.
    cost: Decimal | None = None
    latency_ms: int | None = None
    status: str | None = None
    task_id: uuid.UUID | None = None
    reason_short: str | None = Field(default=None, max_length=280)


@router.post("/model-run", status_code=status.HTTP_201_CREATED)
def record_model_run(body: ModelRunIn, session: Session = Depends(get_session)) -> dict[str, str]:
    session.add(ModelRun(**body.model_dump()))
    session.commit()
    return {"status": "recorded"}


@router.get("/status")
def internal_status(request: Request) -> dict[str, Any]:
    registry = request.app.state.registry
    return {"service": "helm-core", "actions_registered": len(registry.known_types()),
            "owner_configured": bool(request.app.state.owner_id)}
