"""Internal API (ТЗ §7.3). Всё под HMAC service auth.

Ключевой эндпоинт — `/internal/inbound`. Через него Hermes обязан
зарегистрировать входящее ДО первого обращения к модели (§9.3, A-DoD п.2).
Если Control Plane недоступен, Hermes получает ошибку и не исполняет задачу
(A-DoD п.3) — это свойство обеспечивается тем, что регистрация синхронная и
её результат нужен для продолжения, а не тем, что Hermes «должен вести себя
хорошо».
"""

from __future__ import annotations

import base64
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ingest import IngestService, NotOwner
from ..knowledge.batch_intake import (
    ArchiveTooLarge, batch_resolve_outcome_text, cancel_remaining, disable_created_sources,
    resolve_batch_domain, retry_failed, stage_batch,
)
from ..knowledge.chat_intake import (
    ATTACHMENT_TOO_LARGE_NOTICE, AttachmentTooLarge, format_domain_menu,
    resolve_outcome_text, resolve_pending_domain, stage_attachment,
)
from ..knowledge.memory import try_remember
from ..knowledge.onboarding import create_invite
from ..knowledge.probe import probe
from ..models import ModelRun, Task, TaskEvent, TaskStatus, utcnow
from ..outbox import enqueue
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


class KnowledgeProbeIn(BaseModel):
    query: str = Field(min_length=1)
    domain: str | None = None


@router.post("/knowledge/probe")
def knowledge_probe(body: KnowledgeProbeIn,
                    session: Session = Depends(get_session)) -> dict[str, Any]:
    """Free-first Knowledge Probe (§14.11), вызывается ДО LLM.

    `helm-control` (Telegram) и `/hooks/max` — единственные вызывающие;
    оба обязаны дёргать это ДО обращения к Hermes, не после. LOCAL_ANSWER
    уже залогирован в `knowledge_answer_runs` внутри `probe()` — здесь
    просто отдаём результат вызывающей стороне, чтобы та решила, слать
    ли ответ напрямую или пропускать сообщение к модели.
    """
    result = probe(session, query=body.query, domain=body.domain)
    session.commit()
    return {"outcome": result.outcome, "mode": result.mode, "answer_text": result.answer_text}


class RememberIn(BaseModel):
    channel: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1)
    origin_message_id: str | None = Field(default=None, max_length=128)


@router.post("/knowledge/remember")
def knowledge_remember(body: RememberIn,
                       session: Session = Depends(get_session)) -> dict[str, Any]:
    """P8.5.12 Micro-Memory «Запомни» — Telegram-сторона (`helm-control`
    работает вне процесса Control Plane и не может звать `try_remember()`
    напрямую), тот же HMAC-паттерн, что `/internal/knowledge/probe`.

    `status: "not_command"` означает «это сообщение не про Remember» —
    вызывающая сторона продолжает обычный `_probe_local_answer`/chief
    путь как раньше, `text` в ответе для этого случая — `None`.
    """
    outcome = try_remember(session, channel=body.channel, text=body.text,
                           origin_message_id=body.origin_message_id)
    session.commit()
    return {"status": outcome.status, "text": outcome.text}


class AttachmentStageIn(BaseModel):
    channel: str = Field(min_length=1, max_length=32)
    #: Байты файла, base64 — HTTP JSON не переносит бинарные данные
    #: напрямую. Лимит размера всё равно проверяет stage_attachment() на
    #: РАСКОДИРОВАННЫХ байтах (MAX_ATTACHMENT_BYTES), не на длине base64.
    data_base64: str = Field(min_length=1)
    original_filename: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=128)
    caption: str | None = None


@router.post("/knowledge/attachment/stage")
def knowledge_attachment_stage(body: AttachmentStageIn,
                               session: Session = Depends(get_session)) -> dict[str, Any]:
    """P8.5.7 Telegram-сторона: `helm-control` работает вне процесса
    Control Plane (хост Hermes, свой venv) и не может звать
    `chat_intake.py` напрямую — этот эндпоинт даёт то же самое, что
    `/hooks/max` делает in-process, по HMAC-подписанному HTTP (тот же
    паттерн, что `/internal/inbound`/`/internal/knowledge/probe`).
    """
    try:
        data = base64.b64decode(body.data_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"data_base64 не декодируется: {exc}")

    try:
        pending = stage_attachment(session, channel=body.channel, data=data,
                                   original_filename=body.original_filename,
                                   mime_type=body.mime_type, caption=body.caption)
    except AttachmentTooLarge:
        session.rollback()
        return {"status": "too_large", "text": ATTACHMENT_TOO_LARGE_NOTICE}
    session.commit()
    return {"status": "staged", "pending_id": str(pending.id),
            "text": format_domain_menu(pending.original_filename)}


class AttachmentResolveIn(BaseModel):
    channel: str = Field(min_length=1, max_length=32)
    reply_text: str = Field(min_length=1)
    recipient: str | None = Field(default=None, max_length=128)


@router.post("/knowledge/attachment/resolve")
def knowledge_attachment_resolve(body: AttachmentResolveIn,
                                 session: Session = Depends(get_session)) -> dict[str, Any]:
    """Продолжение диалога (шаг 2, P8.5.7) для Telegram — тот же
    `resolve_pending_domain()`, что MAX вызывает in-process. `status:
    "not_pending"` означает «это сообщение не про вложение» — вызывающая
    сторона (helm-control) продолжает обычный `_register_task`/
    `_probe_local_answer` путь как раньше, `text` в ответе для этого
    случая — `None`.
    """
    outcome = resolve_pending_domain(session, channel=body.channel, reply_text=body.reply_text,
                                     recipient=body.recipient)
    session.commit()
    return {"status": outcome.status, "text": resolve_outcome_text(outcome)}


class BatchStageIn(BaseModel):
    channel: str = Field(min_length=1, max_length=32)
    data_base64: str = Field(min_length=1)
    original_filename: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=128)
    recipient: str | None = Field(default=None, max_length=128)


@router.post("/knowledge/batches")
def knowledge_batches_stage(body: BatchStageIn,
                            session: Session = Depends(get_session)) -> dict[str, Any]:
    """v3.7 P8.5.2.1 — ZIP-архив (spec: `POST /internal/knowledge/batches`).
    Тот же HMAC/base64-паттерн, что уже есть у одиночных вложений выше;
    ZIP перехватывается ДО роутера парсеров (§14.4.0), это отдельный
    диалог, не `chat_intake.py`."""
    try:
        data = base64.b64decode(body.data_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"data_base64 не декодируется: {exc}")

    try:
        result = stage_batch(session, channel=body.channel, data=data,
                             original_filename=body.original_filename,
                             mime_type=body.mime_type, recipient=body.recipient)
    except ArchiveTooLarge as exc:
        session.rollback()
        return {"status": "too_large",
               "text": f"Архив слишком большой ({exc.size} байт) — лимит {exc.limit} байт."}
    session.commit()
    return {"status": "staged" if result.waiting_for_domain else "blocked",
            "batch_id": str(result.batch.id), "text": result.text}


class BatchResolveIn(BaseModel):
    channel: str = Field(min_length=1, max_length=32)
    reply_text: str = Field(min_length=1)


@router.post("/knowledge/batches/resolve-domain")
def knowledge_batches_resolve_domain(body: BatchResolveIn,
                                     session: Session = Depends(get_session)) -> dict[str, Any]:
    """Шаг 2 диалога batch (§14.5.1) — тот же `not_pending`-контракт, что
    у `/knowledge/attachment/resolve`: вызывающая сторона (`hooks.py`/
    `helm-control`) продолжает обычный путь, если это не про batch."""
    outcome = resolve_batch_domain(session, channel=body.channel, reply_text=body.reply_text)
    session.commit()
    return {"status": outcome.status, "text": batch_resolve_outcome_text(outcome),
            "batch_id": str(outcome.batch.id) if outcome.batch else None}


@router.post("/knowledge/batches/{batch_id}/retry-failed")
def knowledge_batches_retry_failed(batch_id: uuid.UUID,
                                   session: Session = Depends(get_session)) -> dict[str, Any]:
    batch = retry_failed(session, batch_id)
    session.commit()
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch не найден")
    return {"status": batch.status}


@router.post("/knowledge/batches/{batch_id}/cancel-remaining")
def knowledge_batches_cancel_remaining(batch_id: uuid.UUID,
                                       session: Session = Depends(get_session)) -> dict[str, Any]:
    batch = cancel_remaining(session, batch_id)
    session.commit()
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch не найден")
    return {"status": batch.status}


@router.post("/knowledge/batches/{batch_id}/disable-created-sources")
def knowledge_batches_disable_created_sources(
        batch_id: uuid.UUID, session: Session = Depends(get_session)) -> dict[str, Any]:
    count = disable_created_sources(session, batch_id)
    session.commit()
    return {"disabled_count": count}


class KnowledgeUserInviteIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    timezone: str = "Europe/Moscow"
    locale: str = "ru"
    storage_quota_bytes: int | None = None
    daily_ingest_quota_bytes: int | None = None
    #: §14.3: опциональная предварительная сверка — НЕ замена verified
    #: `from.id` первого приватного контакта с ботом, только доп. слой.
    expected_external_user_id: str | None = Field(default=None, max_length=64)


@router.post("/knowledge/users/invite", status_code=status.HTTP_201_CREATED)
def knowledge_users_invite(body: KnowledgeUserInviteIn, request: Request,
                           session: Session = Depends(get_session)) -> dict[str, Any]:
    """v3.8 §9.0/P8.6.2 — завести нового `KNOWLEDGE_USER` + одноразовый
    инвайт. Panel-фронтенд для этого (P8.6.5, "Система → Пользователи")
    ещё не реализован — до него единственный путь владельца вызвать
    это же самое: HMAC-подписанный вызов этого internal-эндпоинта
    (тот же service secret, что уже используют внутренние скрипты
    деплоя), не открытая наружу форма.
    """
    result = create_invite(
        session, created_by=request.app.state.owner_id, display_name=body.display_name,
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


class OutboundMessage(BaseModel):
    channel: str = Field(min_length=1, max_length=32)
    recipient: str = Field(min_length=1, max_length=128)
    #: Стабильный ключ повтора: при повторной доставке того же ответа
    #: (ретрай плагина, рестарт Hermes) сообщение не задваивается.
    reference: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)


@router.post("/outbound", status_code=status.HTTP_201_CREATED)
def outbound(message: OutboundMessage,
             session: Session = Depends(get_session)) -> dict[str, Any]:
    """Ответ chief-агента в очередь исходящих (§10.3).

    Hermes не ходит в MAX API напрямую: очередь даёт ровно-однократность
    (§30.2 «outbox no duplicate») и переживает недоступность канала, а
    прямая отправка из плагина — нет.
    """
    result = enqueue(session, channel=message.channel, recipient=message.recipient,
                     reference=message.reference,
                     payload_reference={"text": message.text})
    session.commit()
    return {"outbox_id": str(result.message.id), "created": result.created}


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
