"""Panel API (ТЗ §7.3, §10.5.5).

Железное правило раздела: **GET не запускает LLM**. Все ответы собираются из
сохранённого состояния. Это проверяется тестом §30.7 «read GET never triggers
Hermes/LiteLLM», поэтому в модуле нет ни одного импорта модельного клиента —
нарушить правило нельзя случайно, только переписав файл.

Панель также не имеет собственной логики побочных эффектов (§7.3): write-эндпоинты
вызывают тот же action registry и тот же approval engine, что и Telegram.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    ActionTrust, Approval, ApprovalStatus, BudgetDaily, MetricPoint, ModelRun,
    Routine, Task, TaskEvent, TaskStatus, utcnow,
)
from .deps import PanelIdentity, get_session, require_panel_session, require_stepup

router = APIRouter(prefix="/api/panel/v1", tags=["panel"])

#: Разделы брифа §3. Панель не создаёт шестого раздела без решения владельца.
SECTIONS = ("today", "approvals", "tasks", "money", "system")


def _approval_brief(approval: Approval, policy) -> dict[str, Any]:
    """Компактный вид одобрения для списков (бриф §3.1 п.2)."""
    spec = policy.get(approval.action_type)
    return {
        "id": str(approval.id),
        "short_id": approval.short_id,
        "action_type": approval.action_type,
        "title_ru": spec.title_ru,
        "panel_view": spec.panel_view,
        "expires_at": approval.expires_at.isoformat(),
        "requested_at": approval.requested_at.isoformat(),
        # Усечённый хэш: бриф §3.2 просит моноширинный копируемый идентификатор,
        # а не весь SHA256 в мобильной строке.
        "action_hash_short": approval.action_hash[:12],
        "task_id": str(approval.task_id) if approval.task_id else None,
    }


@router.get("/today")
def today(request: Request, session: Session = Depends(get_session),
          identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, Any]:
    """Стартовый экран (бриф §3.1).

    Порядок блоков — по срочности, как в брифе. Каждый блок отдаётся
    отдельным ключом: бриф §3.1 требует, чтобы упавший блок показывал ошибку
    внутри себя, а остальной экран работал, — значит фронт должен уметь
    отрисовать частичный ответ.
    """
    policy = request.app.state.registry._policy
    now = utcnow()

    pending = session.scalars(
        select(Approval)
        .where(Approval.status == ApprovalStatus.PENDING, Approval.expires_at > now)
        .order_by(Approval.expires_at)
    ).all()

    stuck = session.scalars(
        select(Task).where(Task.status == TaskStatus.RUNNING,
                           Task.updated_at < now - timedelta(minutes=30))
    ).all()
    running = session.scalar(
        select(func.count()).select_from(Task).where(Task.status == TaskStatus.RUNNING)
    ) or 0

    budget = session.scalar(
        select(BudgetDaily).where(BudgetDaily.scope == "system")
        .order_by(BudgetDaily.date.desc())
    )

    # «Ночью сделано» — бриф §3.1 п.6: завершённые с 22:00 до сейчас, без
    # LLM-пересказа, только название и стоимость.
    night_start = now.replace(hour=22, minute=0, second=0, microsecond=0)
    if now.hour < 22:
        night_start -= timedelta(days=1)
    overnight = session.scalars(
        select(Task).where(Task.status == TaskStatus.DONE, Task.updated_at >= night_start)
        .order_by(Task.updated_at.desc()).limit(5)
    ).all()

    return {
        "generated_at": now.isoformat(),
        "approvals": {
            "count": len(pending),
            "items": [_approval_brief(a, policy) for a in pending[:3]],
        },
        "money": {
            "spent_today_usd": str(budget.spent_usd) if budget else None,
            "hard_limit_usd": str(budget.hard_limit_usd) if budget else None,
            "kill_switch_active": bool(budget.kill_switch_active) if budget else False,
        },
        "tasks": {
            "running": running,
            "stuck": [
                {"id": str(t.id), "title": t.title_redacted,
                 "stuck_since": t.updated_at.isoformat()} for t in stuck
            ],
        },
        "overnight": [
            {"id": str(t.id), "title": t.title_redacted,
             "finished_at": t.updated_at.isoformat()} for t in overnight
        ],
    }


@router.get("/approvals")
def list_approvals(request: Request, state: str = "pending",
                   session: Session = Depends(get_session),
                   identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, Any]:
    policy = request.app.state.registry._policy
    query = select(Approval).order_by(Approval.requested_at.desc())
    if state == "pending":
        query = query.where(Approval.status == ApprovalStatus.PENDING,
                            Approval.expires_at > utcnow())
    items = session.scalars(query.limit(100)).all()
    return {"items": [_approval_brief(a, policy) for a in items]}


@router.get("/approvals/{approval_id}")
def approval_detail(approval_id: uuid.UUID, request: Request,
                    session: Session = Depends(get_session),
                    identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, Any]:
    """Полная карточка (бриф §3.2).

    Отдаёт суть действия «в его родной форме» и фактический статус каждого
    предусловия. Предусловия проверяются здесь заново, а не берутся из
    сохранённого снимка: бриф прямо называет этот чеклист недекоративным,
    а значит он обязан показывать состояние на сейчас.
    """
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "одобрение не найдено")

    registry = request.app.state.registry
    spec = registry.policy_for(approval.action_type)
    payload = approval.payload_encrypted_or_reference or {}

    preconditions = []
    for name in spec.required_preconditions:
        try:
            registry.check_preconditions(approval.action_type, payload,
                                         _ReadOnlyCtx(str(approval.id)))
            preconditions.append({"name": name, "ok": True, "detail": None})
        except Exception as exc:  # предусловие сообщает причину, а не падает наружу
            preconditions.append({"name": name, "ok": False, "detail": str(exc)})
            break

    trust = session.get(ActionTrust, approval.action_type)
    return {
        **_approval_brief(approval, registry._policy),
        "status": approval.status,
        "action_hash": approval.action_hash,
        "payload": payload,
        "preconditions": preconditions,
        "trust": {
            "supervised_success": trust.supervised_success if trust else 0,
            "threshold": 10,
            "last_incident_at": trust.last_incident_at.isoformat()
            if trust and trust.last_incident_at else None,
        },
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "decided_by_channel": approval.channel,
    }


class _ReadOnlyCtx:
    """Контекст для проверки предусловий на чтение. Ничего не исполняет."""

    def __init__(self, approval_id: str):
        self.approval_id = approval_id
        self.task_id = None
        self.idempotency_key = ""


@router.get("/tasks")
def list_tasks(session: Session = Depends(get_session),
               identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, Any]:
    """Задачи, сгруппированные по состоянию (бриф §3.3)."""
    groups = {"stuck": [], "running": [], "needs_approval": [], "done_today": []}
    now = utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    for task in session.scalars(select(Task).order_by(Task.updated_at.desc()).limit(200)):
        row = {"id": str(task.id), "title": task.title_redacted, "domain": task.domain,
               "status": task.status, "since": task.updated_at.isoformat()}
        if task.status == TaskStatus.RUNNING and task.updated_at < now - timedelta(minutes=30):
            groups["stuck"].append(row)
        elif task.status == TaskStatus.RUNNING:
            groups["running"].append(row)
        elif task.status == TaskStatus.NEEDS_APPROVAL:
            groups["needs_approval"].append(row)
        elif task.status == TaskStatus.DONE and task.updated_at >= today_start:
            groups["done_today"].append(row)
    return groups


@router.get("/tasks/{task_id}")
def task_detail(task_id: uuid.UUID, session: Session = Depends(get_session),
                identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, Any]:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "задача не найдена")

    events = session.scalars(
        select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.timestamp)
    ).all()
    runs = session.scalars(
        select(ModelRun).where(ModelRun.task_id == task_id).order_by(ModelRun.timestamp)
    ).all()
    return {
        "id": str(task.id),
        "title": task.title_redacted,
        "status": task.status,
        "domain": task.domain,
        "risk_level": task.risk_level,
        # Бриф §3.3: «не "активность" с аватарами, а лог».
        "timeline": [
            {"at": e.timestamp.isoformat(), "actor": e.actor, "event": e.event_type,
             "payload": e.payload_redacted} for e in events
        ],
        "model_calls": [
            {"at": r.timestamp.isoformat(), "alias": r.alias, "model": r.concrete_model,
             "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
             "cost_usd": str(r.cost) if r.cost is not None else None,
             "reason_short": r.reason_short} for r in runs
        ],
    }


@router.get("/money")
def money(session: Session = Depends(get_session),
          identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, Any]:
    """Деньги (бриф §3.4). Никаких процентов без абсолюта."""
    now = utcnow()
    rows = session.scalars(
        select(BudgetDaily).where(BudgetDaily.date >= now - timedelta(days=30))
        .order_by(BudgetDaily.date)
    ).all()
    system_today = next((r for r in reversed(rows) if r.scope == "system"), None)

    expensive = session.scalars(
        select(ModelRun).where(ModelRun.reason_short.is_not(None))
        .order_by(ModelRun.timestamp.desc()).limit(20)
    ).all()

    return {
        "today": {
            "spent_usd": str(system_today.spent_usd) if system_today else "0",
            "hard_limit_usd": str(system_today.hard_limit_usd) if system_today else None,
            "soft_limit_usd": str(system_today.soft_limit_usd)
            if system_today and system_today.soft_limit_usd else None,
            "kill_switch_active": bool(system_today.kill_switch_active) if system_today else False,
        },
        "daily": [
            {"date": r.date.date().isoformat(), "scope": r.scope, "spent_usd": str(r.spent_usd)}
            for r in rows
        ],
        "expensive_calls": [
            {"at": r.timestamp.isoformat(), "task_id": str(r.task_id) if r.task_id else None,
             "alias": r.alias, "model": r.concrete_model,
             "cost_usd": str(r.cost) if r.cost is not None else None,
             "reason_short": r.reason_short} for r in expensive
        ],
    }


@router.get("/system")
def system(session: Session = Depends(get_session),
           identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, Any]:
    """Система (бриф §3.5). Только то, у чего есть порог."""
    now = utcnow()
    latest: dict[str, MetricPoint] = {}
    for point in session.scalars(
        select(MetricPoint).where(MetricPoint.timestamp >= now - timedelta(hours=2))
        .order_by(MetricPoint.timestamp)
    ):
        latest[point.metric] = point

    routines = session.scalars(select(Routine).order_by(Routine.name)).all()
    return {
        "resources": [
            {"metric": name, "value": str(p.value), "at": p.timestamp.isoformat(),
             "labels": p.labels} for name, p in sorted(latest.items())
        ],
        "routines": [
            {"id": str(r.id), "name": r.name, "schedule": r.schedule, "enabled": r.enabled,
             "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
             "last_status": r.last_status,
             "consecutive_failures": r.consecutive_failures} for r in routines
        ],
    }


# ── запись: только через action registry, с обязательным step-up ────────────

@router.post("/actions/{approval_id}/approve")
def approve(approval_id: uuid.UUID, request: Request,
            session: Session = Depends(get_session),
            identity: PanelIdentity = Depends(require_panel_session),
            stepup=Depends(require_stepup)) -> dict[str, Any]:
    """Одобрить из панели.

    `require_stepup` проверяет свежую passkey-assertion, привязанную к
    ЭТОМУ approval_id и его хэшу (§10.5.8.1). Панель не исполняет действие
    сама — вызывает тот же ApprovalService, что и Telegram.
    """
    service = request.app.state.approval_service_factory(session)
    stepup.assert_binds(approval_id, session)
    approval = service.decide(approval_id, approve=True, decided_by=identity.owner_id,
                              channel="panel")
    result = service.execute_approved(approval.id)
    session.commit()
    return {"status": approval.status, "result": result}


@router.post("/actions/{approval_id}/reject")
def reject(approval_id: uuid.UUID, request: Request, reason: str | None = None,
           session: Session = Depends(get_session),
           identity: PanelIdentity = Depends(require_panel_session),
           stepup=Depends(require_stepup)) -> dict[str, Any]:
    service = request.app.state.approval_service_factory(session)
    stepup.assert_binds(approval_id, session)
    approval = service.decide(approval_id, approve=False, decided_by=identity.owner_id,
                              channel="panel")
    session.commit()
    return {"status": approval.status}
