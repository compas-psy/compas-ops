"""Panel API (ТЗ §7.3, §10.5.5).

Железное правило раздела: **GET не запускает LLM**. Все ответы собираются из
сохранённого состояния. Это проверяется тестом §30.7 «read GET never triggers
Hermes/LiteLLM», поэтому в модуле нет ни одного импорта модельного клиента —
нарушить правило нельзя случайно, только переписав файл.

Панель также не имеет собственной логики побочных эффектов (§7.3): write-эндпоинты
вызывают тот же action registry и тот же approval engine, что и Telegram.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pydantic import BaseModel, Field

from ..knowledge.onboarding import create_invite, reactivate_user, suspend_user
from ..knowledge.tenancy import bind_knowledge_user
from ..models import (
    ActionTrust, Approval, ApprovalStatus, BudgetDaily, KnowledgeChannelIdentity,
    KnowledgeMemory, KnowledgeMemoryStatus, KnowledgeSource, KnowledgeUser, KnowledgeUserRole,
    KnowledgeUserStatus, KnowledgeUserUsage, MetricPoint, ModelRun, PanelEnrollmentToken,
    Routine, Task, TaskEvent, TaskStatus, utcnow,
)
from .deps import (
    PanelIdentity, get_session, knowledge_principal, require_owner_session,
    require_panel_session, require_stepup,
)

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
          identity: PanelIdentity = Depends(require_owner_session)) -> dict[str, Any]:
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
                   identity: PanelIdentity = Depends(require_owner_session)) -> dict[str, Any]:
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
                    identity: PanelIdentity = Depends(require_owner_session)) -> dict[str, Any]:
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
               identity: PanelIdentity = Depends(require_owner_session)) -> dict[str, Any]:
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
                identity: PanelIdentity = Depends(require_owner_session)) -> dict[str, Any]:
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
          identity: PanelIdentity = Depends(require_owner_session)) -> dict[str, Any]:
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
           identity: PanelIdentity = Depends(require_owner_session)) -> dict[str, Any]:
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


# ── Система → Пользователи (v3.8 §14.3, P8.6.5) ─────────────────────────────
#
# Спека прямо запрещает владельцу «normal content browser across users»:
# управление людьми — не доступ к их Второму мозгу. Здесь это не рантайм-
# проверка, а свойство того, какие таблицы вообще названы в коде — все
# эндпоинты ниже читают ТОЛЬКО реестр тенантов (`knowledge_users`,
# `knowledge_channel_identities`, `knowledge_user_usage`) и ни одной
# tenant-scoped таблицы с контентом (`knowledge_sources`,
# `knowledge_memories`, …). Дотянуться до чужого документа отсюда нельзя
# не потому, что что-то это ловит, а потому, что запроса нет.
#
# Каждая запись требует свежей passkey-церемонии, привязанной к ЭТОЙ
# операции и ЭТОМУ пользователю (`StepUp.assert_scope()`) — то же правило
# §10.5.8.1, что защищает одобрения: подтверждение, полученное на
# «пригласить», не подойдёт к «приостановить», а полученное на
# приостановку A — к приостановке B.

#: У приглашения нет цели-пользователя: он ещё не существует.
SCOPE_INVITE = "panel:users:invite"

#: Срок enrollment-токена панели. Короче суточного инвайта в бот: токен
#: доставляется владельцем вручную и используется сразу, а не «когда-нибудь
#: за день».
PANEL_INVITE_TTL = timedelta(hours=2)

#: Сколько записей отдаёт Knowledge-оболочка за раз. Пагинации нет —
#: заводить её до появления живого корпуса второго пользователя незачем.
SHELL_PAGE_SIZE = 100


def _knowledge_user_view(user: KnowledgeUser, usage: KnowledgeUserUsage | None,
                         identities: list[KnowledgeChannelIdentity]) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "role": user.role,
        "status": user.status,
        "display_name": user.display_name,
        "locale": user.locale,
        "timezone": user.timezone,
        "allow_paid_ai": user.allow_paid_ai,
        "storage_quota_bytes": user.storage_quota_bytes,
        "daily_ingest_quota_bytes": user.daily_ingest_quota_bytes,
        # Счётчики сформированных записей ведёт `quotas.record_entry_formed()`
        # (распоряжение владельца 30.08.2026, «принцип Obsidian»: считаем
        # сформированные записи, архивирование их не уменьшает).
        # `queued_jobs` сюда не выводится: он вычисляется на лету
        # (`check_queue_depth`), хранить его копию было бы вторым источником
        # правды о том же.
        "storage_bytes": usage.storage_bytes if usage else 0,
        "ingest_bytes_today": usage.ingest_bytes_today if usage else 0,
        "sources_count": usage.sources_count if usage else 0,
        "memories_count": usage.memories_count if usage else 0,
        "created_at": user.created_at.isoformat(),
        "activated_at": user.activated_at.isoformat() if user.activated_at else None,
        "suspended_at": user.suspended_at.isoformat() if user.suspended_at else None,
        # Без `external_user_id`: владельцу для управления учёткой нужен
        # факт «канал привязан», а не Telegram-идентификатор живого
        # человека (CLAUDE.md §5.2 — минимум персональных данных).
        "channels": [
            {"channel": i.channel, "verified_at": i.verified_at.isoformat(),
             "is_primary": i.is_primary}
            for i in identities if i.revoked_at is None
        ],
    }


@router.get("/users")
def list_knowledge_users(session: Session = Depends(get_session),
                         identity: PanelIdentity = Depends(require_owner_session),
                         ) -> dict[str, Any]:
    """§14.3 «Система → Пользователи» — метаданные и квоты, не контент.

    `SYSTEM_OWNER` в списке присутствует (это тоже строка реестра, и его
    расход места владельцу видеть полезно), но не может быть suspend'нут —
    см. `_require_manageable_user()`.
    """
    users = session.scalars(select(KnowledgeUser).order_by(KnowledgeUser.created_at)).all()
    usages = {u.knowledge_user_id: u for u in session.scalars(select(KnowledgeUserUsage))}
    identities: dict[uuid.UUID, list[KnowledgeChannelIdentity]] = {}
    for record in session.scalars(select(KnowledgeChannelIdentity)):
        identities.setdefault(record.knowledge_user_id, []).append(record)

    return {"items": [
        _knowledge_user_view(u, usages.get(u.id), identities.get(u.id, []))
        for u in users
    ]}


class PanelInviteIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    timezone: str = Field(default="Europe/Moscow", max_length=64)
    locale: str = Field(default="ru", max_length=8)
    storage_quota_bytes: int | None = Field(default=None, ge=0)
    daily_ingest_quota_bytes: int | None = Field(default=None, ge=0)
    expected_external_user_id: str | None = Field(default=None, max_length=64)


@router.post("/users/invite", status_code=status.HTTP_201_CREATED)
def invite_knowledge_user(body: PanelInviteIn, request: Request,
                          session: Session = Depends(get_session),
                          identity: PanelIdentity = Depends(require_owner_session),
                          stepup=Depends(require_stepup)) -> dict[str, Any]:
    """Пригласить нового `KNOWLEDGE_USER` (P8.6.5 заменяет собой стенд-ин
    `POST /internal/knowledge/users/invite`, но не отменяет его —
    internal-путь остаётся для скриптов).

    Свежий passkey обязателен: приглашение заводит человеку доступ ко
    Второму мозгу, это не read-only просмотр. `assert_binds()` не
    вызывается — он привязывает церемонию к конкретному `approval_id`,
    а здесь одобрения нет; требуется именно свежесть подтверждения
    (§10.5.8: 60 секунд, одноразово).

    Сырой токен возвращается ЕДИНСТВЕННЫЙ раз — в БД только его хэш.
    """
    stepup.assert_scope(SCOPE_INVITE)
    result = create_invite(
        session, created_by=identity.owner_id, display_name=body.display_name,
        timezone=body.timezone, locale=body.locale,
        storage_quota_bytes=body.storage_quota_bytes,
        daily_ingest_quota_bytes=body.daily_ingest_quota_bytes,
        expected_external_user_id=body.expected_external_user_id,
    )
    session.commit()
    bot_username = request.app.state.settings.knowledge_telegram_bot_username
    deep_link = (f"https://t.me/{bot_username}?start=kb_{result.raw_token}"
                 if bot_username else None)
    return {
        "knowledge_user_id": str(result.user.id),
        "invite_token": result.raw_token,
        "deep_link": deep_link,
        "expires_at": result.invite.expires_at.isoformat(),
    }


def _require_manageable_user(session: Session, knowledge_user_id: uuid.UUID) -> KnowledgeUser:
    """`SYSTEM_OWNER` не управляется через этот раздел.

    Suspend владельца перекрыл бы доступ к его собственному HELM'у из
    интерфейса, задуманного для управления ЧУЖИМИ учётками, и сделал бы
    это без единого способа откатить — панель после этого недоступна.
    """
    user = session.get(KnowledgeUser, knowledge_user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "пользователь не найден")
    if user.role == KnowledgeUserRole.SYSTEM_OWNER:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "SYSTEM_OWNER не управляется через раздел «Пользователи»")
    return user


@router.post("/users/{knowledge_user_id}/suspend")
def suspend_knowledge_user(knowledge_user_id: uuid.UUID,
                           session: Session = Depends(get_session),
                           identity: PanelIdentity = Depends(require_owner_session),
                           stepup=Depends(require_stepup)) -> dict[str, Any]:
    """§14.3 «Suspend/offboard»: доступ к боту/панели закрыт, данные
    сохранены. Необратимого удаления здесь нет — оно RED и отдельное."""
    stepup.assert_scope(f"panel:users:suspend:{knowledge_user_id}")
    _require_manageable_user(session, knowledge_user_id)
    outcome = suspend_user(session, knowledge_user_id)
    session.commit()
    return {"status": outcome.status, "knowledge_user_id": str(knowledge_user_id)}


@router.post("/users/{knowledge_user_id}/reactivate")
def reactivate_knowledge_user(knowledge_user_id: uuid.UUID,
                              session: Session = Depends(get_session),
                              identity: PanelIdentity = Depends(require_owner_session),
                              stepup=Depends(require_stepup)) -> dict[str, Any]:
    stepup.assert_scope(f"panel:users:reactivate:{knowledge_user_id}")
    _require_manageable_user(session, knowledge_user_id)
    outcome = reactivate_user(session, knowledge_user_id)
    session.commit()
    return {"status": outcome.status, "knowledge_user_id": str(knowledge_user_id)}


class PanelQuotaIn(BaseModel):
    """`None` — снять лимит; поле, которого нет в теле, не меняется."""

    storage_quota_bytes: int | None = Field(default=None, ge=0)
    daily_ingest_quota_bytes: int | None = Field(default=None, ge=0)


@router.post("/users/{knowledge_user_id}/quota")
def set_knowledge_user_quota(knowledge_user_id: uuid.UUID, body: PanelQuotaIn,
                             session: Session = Depends(get_session),
                             identity: PanelIdentity = Depends(require_owner_session),
                             stepup=Depends(require_stepup)) -> dict[str, Any]:
    """§14.3 «metadata/quota» — правка квот из панели вместо
    единственного прежнего момента (создание инвайта)."""
    stepup.assert_scope(f"panel:users:quota:{knowledge_user_id}")
    user = _require_manageable_user(session, knowledge_user_id)
    fields = body.model_dump(exclude_unset=True)
    for field, value in fields.items():
        setattr(user, field, value)
    session.commit()
    return {"knowledge_user_id": str(knowledge_user_id),
            "storage_quota_bytes": user.storage_quota_bytes,
            "daily_ingest_quota_bytes": user.daily_ingest_quota_bytes}


@router.post("/users/{knowledge_user_id}/panel-invite", status_code=status.HTTP_201_CREATED)
def invite_knowledge_user_to_panel(knowledge_user_id: uuid.UUID, request: Request,
                                   session: Session = Depends(get_session),
                                   identity: PanelIdentity = Depends(require_owner_session),
                                   stepup=Depends(require_stepup)) -> dict[str, Any]:
    """Выдать KNOWLEDGE_USER одноразовый enrollment-токен для входа в панель.

    Решение владельца от 30.08.2026: доступ в панель — ОТДЕЛЬНЫМ токеном, не
    через Dedicated Knowledge Bot. Токен доставляет человеку сам владелец,
    вне HELM; система за этот канал не отвечает и потому даёт короткий
    срок и однократность.

    Только для уже ACTIVE-пользователя: панель не должна становиться
    обходным входом для того, кому бот ещё (или уже) отказывает.
    """
    stepup.assert_scope(f"panel:users:panel-invite:{knowledge_user_id}")
    user = _require_manageable_user(session, knowledge_user_id)
    if user.status != KnowledgeUserStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "сначала пользователь должен подключиться к боту по инвайту")

    settings = request.app.state.settings
    raw_token = secrets.token_urlsafe(32)
    session.add(PanelEnrollmentToken(
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        owner_id=knowledge_principal(knowledge_user_id),
        expires_at=utcnow() + PANEL_INVITE_TTL,
    ))
    session.commit()
    return {
        "knowledge_user_id": str(knowledge_user_id),
        # Единственный раз, когда токен существует вне сообщения владельцу.
        "enrollment_token": raw_token,
        "panel_url": f"https://{settings.panel_rp_id}/login?step=knowledge-enroll",
        "expires_at": (utcnow() + PANEL_INVITE_TTL).isoformat(),
    }


@router.get("/session")
def session_role(identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, Any]:
    """Кто вошёл — чтобы фронт знал, какую оболочку рисовать.

    Отдельный дешёвый эндпоинт вместо «попробовать owner-раздел и поймать
    403»: роль решает, какой интерфейс показать, и узнавать её ошибкой —
    значит гарантировать мигание чужого экрана перед отказом.
    """
    return {
        "role": (KnowledgeUserRole.KNOWLEDGE_USER if identity.knowledge_user_id
                 else KnowledgeUserRole.SYSTEM_OWNER),
    }


# ── Knowledge-оболочка (v3.8 §14.3 "KNOWLEDGE_USER: own memories/docs only") ──


@router.get("/knowledge")
def knowledge_shell(session: Session = Depends(get_session),
                    identity: PanelIdentity = Depends(require_panel_session)) -> dict[str, Any]:
    """Свой Второй мозг — и ничей больше.

    Единственный эндпоинт панели, доступный обеим ролям, и именно потому,
    что тенант берётся ИЗ СЕССИИ, а не из параметра запроса: подставить
    чужой `knowledge_user_id` физически нечем. Владелец видит здесь свой
    корпус (это заодно закрывает P8.5.8 «Panel строка Knowledge» для него),
    KNOWLEDGE_USER — свой.

    `bind_knowledge_user()` привязывает GUC до единого запроса, поэтому
    промах explicit-предиката всё равно был бы пойман RLS.
    """
    tenant_id = bind_knowledge_user(session, identity.knowledge_user_id)

    memories = session.scalars(
        select(KnowledgeMemory)
        .where(KnowledgeMemory.knowledge_user_id == tenant_id,
               KnowledgeMemory.status != KnowledgeMemoryStatus.DELETED)
        .order_by(KnowledgeMemory.created_at.desc()).limit(SHELL_PAGE_SIZE)
    ).all()
    sources = session.scalars(
        select(KnowledgeSource)
        .where(KnowledgeSource.knowledge_user_id == tenant_id)
        .order_by(KnowledgeSource.created_at.desc()).limit(SHELL_PAGE_SIZE)
    ).all()
    usage = session.get(KnowledgeUserUsage, tenant_id)
    user = session.get(KnowledgeUser, tenant_id)

    return {
        "role": user.role if user else None,
        "display_name": user.display_name if user else None,
        "timezone": user.timezone if user else None,
        "usage": {
            "storage_bytes": usage.storage_bytes if usage else 0,
            "sources_count": usage.sources_count if usage else 0,
            "memories_count": usage.memories_count if usage else 0,
            "storage_quota_bytes": user.storage_quota_bytes if user else None,
        },
        "memories": [
            {"id": str(m.id), "kind": m.kind, "text": m.canonical_text, "status": m.status,
             "created_at": m.created_at.isoformat(),
             "expires_at": m.expires_at.isoformat() if m.expires_at else None}
            for m in memories
        ],
        "sources": [
            {"id": str(s.id), "title": s.original_filename, "domain": s.domain,
             "status": s.status, "created_at": s.created_at.isoformat()}
            for s in sources
        ],
    }


# ── запись: только через action registry, с обязательным step-up ────────────

@router.post("/actions/{approval_id}/approve")
def approve(approval_id: uuid.UUID, request: Request,
            session: Session = Depends(get_session),
            identity: PanelIdentity = Depends(require_owner_session),
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
           identity: PanelIdentity = Depends(require_owner_session),
           stepup=Depends(require_stepup)) -> dict[str, Any]:
    service = request.app.state.approval_service_factory(session)
    stepup.assert_binds(approval_id, session)
    approval = service.decide(approval_id, approve=False, decided_by=identity.owner_id,
                              channel="panel")
    session.commit()
    return {"status": approval.status}
