"""Публичные вебхуки (ТЗ §10.1). Единственный на сегодня — MAX.

Этот роутер — единственное место в Control Plane, куда приходит запрос
снаружи без HMAC service auth: MAX подписать его нашим секретом не может.
Аутентификация здесь — общий секрет вебхука в заголовке (§10.1 п.1), и
поэтому ниже нет ни одной ветки, которая делает что-либо до его проверки.

Порядок шагов взят из §10.1 дословно: проверить секрет → проверить
владельца → дедуплицировать → зарегистрировать task → Knowledge Probe →
вызвать локальный chief API. Порядок важен, а не декоративен: вердикт
дедупликации нужен ДО обращения к Hermes, иначе на схлопнутом дубле
chief ответил бы во второй раз на уже отвеченный вопрос; Probe (§14.11,
v3.4) — тоже до Hermes, а не «совет поискать» — LOCAL_ANSWER отвечает
владельцу напрямую и chief вообще не вызывается.

Вызов Hermes (`HermesBridge.deliver`, §10.2) уходит в ФОНОВУЮ задачу, а
не в тело этого обработчика: это синхронный HTTP-запрос к
`/v1/responses`, который держится столько же, сколько ход агента —
минуты, если тот пользуется инструментами. MAX ждёт ответ на вебхук
секунды, не минуты; обработчик обязан вернуть 2xx сразу после
регистрации задачи, а доставка ответа владельцу идёт отдельно, через
ту же очередь outbox, что и любое другое исходящее (§10.3).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import State

from ..channels.max import (
    MaxAttachmentUnsupported, WEBHOOK_SECRET_HEADER, download_attachment,
    parse_attachment, parse_message_created, verify_webhook_secret,
)
from ..hermes_bridge import HermesUnavailable
from ..ingest import IngestService, record_channel_event_once
from ..knowledge.chat_intake import (
    AttachmentTooLarge, format_domain_menu, resolve_pending_domain, stage_attachment,
)
from ..knowledge.probe import probe, query_hash
from ..models import KnowledgeAnswerRun, KnowledgePendingAttachment, TaskEvent
from ..outbox import enqueue
from .deps import get_session

router = APIRouter(prefix="/hooks", tags=["hooks"])

logger = logging.getLogger(__name__)

#: Транспортное уведомление при недоступном Hermes (§10.3: «owner получает
#: transport-level сообщение, если MAX API доступен»). Отправляется не
#: моделью и от модели не зависит — в этом весь смысл fallback-канала.
HERMES_DOWN_NOTICE = (
    "HELM принял сообщение, но агент сейчас недоступен. "
    "Задача зарегистрирована и будет выполнена после восстановления."
)

#: P8.5.7. `download_attachment()`/`parse_attachment()` не подтверждены
#: живым тестом (см. их docstring в channels/max.py) — этот текст владелец
#: увидит, если реальная форма MAX-вложения разойдётся с ожидаемой.
ATTACHMENT_DOWNLOAD_FAILED_NOTICE = (
    "Не смог скачать вложение — попробуйте прислать ещё раз или сообщите "
    "об ошибке."
)
ATTACHMENT_TOO_LARGE_NOTICE = "Файл слишком большой — не сохранён."
ATTACHMENT_MISSING_NOTICE = "Файл потерян на сервере — пришлите, пожалуйста, ещё раз."
ATTACHMENT_MOVE_FAILED_NOTICE = (
    "Не получилось сохранить файл — попробуйте выбрать домен ещё раз."
)
ATTACHMENT_CANCELLED_NOTICE = "Хорошо, не сохраняю."


def _run_chief_and_enqueue_reply(state: State, *, task_id: str, owner_id: str,
                                 chat_id: str, text: str) -> None:
    """Фоновая задача: вызвать chief-агента, поставить ответ в очередь.

    Собственная сессия БД — сессия запроса закрывается вместе с ответом
    вебхуку, а эта задача переживает его на минуты. Синхронная функция,
    не корутина: `BackgroundTasks` прогоняет её в threadpool, что здесь и
    нужно — `HermesBridge.deliver` блокирует поток на время HTTP-вызова.
    """
    with state.session_factory() as session:
        try:
            reply_text = state.hermes_bridge.deliver(owner_id=owner_id, text=text)
        except HermesUnavailable as exc:
            # НАЙДЕНО 29.08.2026: причина падения раньше писалась только в
            # TaskEvent (БД) — `docker compose logs` не показывал вообще
            # ничего, и диагностика требовала прямого psql-запроса вместо
            # обычного просмотра лога. Тип исключения безопасен для лога
            # (без текста сообщения владельца), полный текст — в TaskEvent.
            logger.warning("hooks/max: chief недоступен, task_id=%s тип=%s",
                           task_id, type(exc).__name__)
            session.add(TaskEvent(
                task_id=task_id, actor="control-plane",
                event_type="task.hermes_unavailable",
                payload_redacted={"channel": "max", "error": str(exc)},
            ))
            enqueue(session, channel="max", recipient=chat_id,
                    reference=f"hermes-down:{task_id}",
                    payload_reference={"text": HERMES_DOWN_NOTICE})
            session.commit()
            return

        # §14.14 paid-avoidance metric: до этой точки Probe уже вернул
        # NEEDS_REASONING (вызывающая сторона иначе не дошла бы сюда) — раз
        # Hermes реально вызван и ответил, это платная эскалация (C1),
        # логируется постфактум, потому что латентность/факт успеха
        # известны только сейчас.
        session.add(KnowledgeAnswerRun(
            query_hash=query_hash(text), domain=None, mode="C1",
            paid_ai_used=True, evidence_count=0,
        ))
        enqueue(session, channel="max", recipient=chat_id,
                reference=f"max-reply:{task_id}",
                payload_reference={"text": reply_text})
        session.commit()


@router.post("/max")
async def max_webhook(request: Request, response: Response, background: BackgroundTasks,
                      session: Session = Depends(get_session)) -> dict[str, Any]:
    if not verify_webhook_secret(request.app.state.max_webhook_secret,
                                 request.headers.get(WEBHOOK_SECRET_HEADER)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "секрет вебхука не совпадает")

    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "тело не является JSON")
    if not isinstance(update, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "тело не является объектом")

    inbound = parse_message_created(update)
    if inbound is None:
        # Не наш тип события или неполное сообщение: 200, чтобы MAX не
        # ретраил то, что мы игнорируем сознательно.
        return {"status": "ignored"}

    # §10.1 п.2. Сверка идёт с MAX-идентификатором владельца, а РЕГИСТРАЦИЯ —
    # под канонической identity (`owner_id`): у владельца в MAX другое число,
    # чем в Telegram, и если завести задачу под MAX-числом, cross-channel
    # дедуп (§10.4) не сработает никогда — normalized_hash считается вместе
    # с owner_id, и хэши одного и того же вопроса из двух каналов разойдутся.
    max_owner_id = request.app.state.max_owner_id
    if not max_owner_id or inbound.sender_id != max_owner_id:
        # Логируется ТОЛЬКО идентификатор отправителя, никогда текст: это
        # и признак чужого обращения к вебхуку, и единственный способ
        # узнать собственный MAX-id владельца при первичной настройке —
        # он не совпадает с Telegram-id и нигде больше не показывается.
        logger.warning("hooks/max: сообщение от не-владельца, sender_id=%s",
                       inbound.sender_id)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "отправитель не владелец")

    # P8.5.7: вложения и двухшаговый диалог выбора домена ОБХОДЯТ обычный
    # register()/probe/chief путь целиком — сообщение с файлом или ответ на
    # вопрос "какой домен" никогда не должно дойти до chief как обычный
    # текст. `has_pending` — read-only проверка ДО дедупа: она решает, идёт
    # ли это сообщение вообще в подсистему вложений; `record_channel_event_
    # once` вызывается ТОЛЬКО внутри этой ветки (никогда для обычного
    # текста) — иначе конфликтует с `register()`'s собственной записью
    # `ChannelEvent` для того же external_message_id (см. docstring
    # `record_channel_event_once`).
    has_pending = session.scalar(
        select(KnowledgePendingAttachment.id)
        .where(KnowledgePendingAttachment.channel == "max")
        .limit(1)
    ) is not None

    if inbound.attachments or has_pending:
        if record_channel_event_once(session, channel="max",
                                     external_message_id=inbound.message_id,
                                     owner_id=request.app.state.owner_id):
            session.commit()
            return {"status": "duplicate"}

        if inbound.attachments:
            try:
                attachment = parse_attachment(inbound.attachments[0])
                data = download_attachment(attachment.url)
            except (MaxAttachmentUnsupported, OSError) as exc:
                logger.warning("hooks/max: не удалось скачать вложение: %s", exc)
                enqueue(session, channel="max", recipient=inbound.chat_id,
                        reference=f"attachment-failed:{inbound.message_id}",
                        payload_reference={"text": ATTACHMENT_DOWNLOAD_FAILED_NOTICE})
                session.commit()
                return {"status": "attachment_failed"}

            try:
                pending = stage_attachment(session, channel="max", data=data,
                                           original_filename=attachment.filename,
                                           mime_type=None, caption=inbound.text)
            except AttachmentTooLarge:
                enqueue(session, channel="max", recipient=inbound.chat_id,
                        reference=f"attachment-too-large:{inbound.message_id}",
                        payload_reference={"text": ATTACHMENT_TOO_LARGE_NOTICE})
                session.commit()
                return {"status": "attachment_too_large"}

            enqueue(session, channel="max", recipient=inbound.chat_id,
                    reference=f"attachment-staged:{pending.id}",
                    payload_reference={"text": format_domain_menu(pending.original_filename)})
            session.commit()
            return {"status": "attachment_staged", "pending_id": str(pending.id)}

        resolved = resolve_pending_domain(session, channel="max", reply_text=inbound.text,
                                          recipient=inbound.chat_id)
        if resolved.status == "ingested":
            enqueue(session, channel="max", recipient=inbound.chat_id,
                    reference=f"attachment-ingested:{resolved.result.source.id}",
                    payload_reference={"text": (
                        f"Сохранено в «{resolved.result.source.domain}». "
                        "Разбор запущен, появится в базе знаний в фоне."
                    )})
            session.commit()
            return {"status": "attachment_ingested"}
        if resolved.status == "cancelled":
            enqueue(session, channel="max", recipient=inbound.chat_id,
                    reference=f"attachment-cancelled:{resolved.pending.id}",
                    payload_reference={"text": ATTACHMENT_CANCELLED_NOTICE})
            session.commit()
            return {"status": "attachment_cancelled"}
        if resolved.status == "missing":
            enqueue(session, channel="max", recipient=inbound.chat_id,
                    reference=f"attachment-missing:{resolved.pending.id}",
                    payload_reference={"text": ATTACHMENT_MISSING_NOTICE})
            session.commit()
            return {"status": "attachment_missing"}
        if resolved.status == "failed":
            # Файл остаётся в spool, pending НЕ снят — можно повторить
            # выбор домена без повторной отправки файла (message_id
            # следующей попытки будет новым, record_channel_event_once её
            # не заблокирует).
            enqueue(session, channel="max", recipient=inbound.chat_id,
                    reference=f"attachment-move-failed:{resolved.pending.id}:{inbound.message_id}",
                    payload_reference={"text": ATTACHMENT_MOVE_FAILED_NOTICE})
            session.commit()
            return {"status": "attachment_move_failed"}
        # status == "invalid": повторяем меню — reference несёт message_id,
        # иначе повторный неверный ответ не долетел бы (exactly-once по
        # reference в outbox, а pending.id один и тот же на все попытки).
        enqueue(session, channel="max", recipient=inbound.chat_id,
                reference=f"attachment-retry:{resolved.pending.id}:{inbound.message_id}",
                payload_reference={"text": format_domain_menu(resolved.pending.original_filename)})
        session.commit()
        return {"status": "attachment_domain_invalid"}

    service = IngestService(session, owner_id=request.app.state.owner_id)
    result = service.register(channel="max", external_message_id=inbound.message_id,
                              owner_id=request.app.state.owner_id, text=inbound.text)

    if result.dedup_reason == "cross_channel_duplicate":
        # Молчаливое схлопывание — решение владельца от 29.08.2026.
        # Тот же вопрос уже дошёл до chief другим каналом и ответ придёт
        # там; второе «я это уже видел» в MAX — шум, а не сервис.
        session.commit()
        return {"status": "collapsed", "task_id": str(result.task.id)}

    if result.dedup_reason == "same_external_message_id":
        # Переотправка того же апдейта транспортом. Задача уже заведена и
        # уже отдана chief при первой доставке — повторять нечего.
        session.commit()
        return {"status": "duplicate", "task_id": str(result.task.id)}

    task_id = str(result.task.id)
    session.commit()

    # §14.11: бесплатный локальный путь ДО платной модели. LOCAL_ANSWER
    # уже залогирован в knowledge_answer_runs внутри probe() — здесь
    # только доставка; chief вообще не вызывается.
    probe_result = probe(session, query=result.text)
    if probe_result.outcome == "LOCAL_ANSWER":
        enqueue(session, channel="max", recipient=inbound.chat_id,
                reference=f"knowledge-probe:{task_id}",
                payload_reference={"text": probe_result.answer_text})
        session.commit()
        return {"status": "local_answer", "task_id": task_id}

    background.add_task(_run_chief_and_enqueue_reply, request.app.state,
                        task_id=task_id, owner_id=request.app.state.owner_id,
                        chat_id=inbound.chat_id, text=result.text)
    response.status_code = status.HTTP_202_ACCEPTED
    return {"status": "accepted", "task_id": task_id}
