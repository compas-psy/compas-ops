"""Публичные вебхуки (ТЗ §10.1). Единственный на сегодня — MAX.

Этот роутер — единственное место в Control Plane, куда приходит запрос
снаружи без HMAC service auth: MAX подписать его нашим секретом не может.
Аутентификация здесь — общий секрет вебхука в заголовке (§10.1 п.1), и
поэтому ниже нет ни одной ветки, которая делает что-либо до его проверки.

Порядок шагов взят из §10.1 дословно: проверить секрет → проверить
владельца → дедуплицировать → зарегистрировать task → вызвать локальный
chief API. Порядок важен, а не декоративен: вердикт дедупликации нужен
ДО обращения к Hermes, иначе на схлопнутом дубле chief ответил бы во
второй раз на уже отвеченный вопрос.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..channels.max import WEBHOOK_SECRET_HEADER, parse_message_created, verify_webhook_secret
from ..hermes_bridge import HermesUnavailable
from ..ingest import IngestService
from ..models import TaskEvent
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


@router.post("/max")
async def max_webhook(request: Request, response: Response,
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

    try:
        request.app.state.hermes_bridge.deliver(
            task_id=str(result.task.id), channel="max",
            chat_id=inbound.chat_id, text=result.text,
        )
    except HermesUnavailable as exc:
        # §10.3: task остаётся REGISTERED, владелец получает транспортное
        # уведомление, повтор — после восстановления Hermes.
        session.add(TaskEvent(
            task_id=result.task.id, actor="control-plane",
            event_type="task.hermes_unavailable",
            payload_redacted={"channel": "max", "error": str(exc)},
        ))
        enqueue(session, channel="max", recipient=inbound.chat_id,
                reference=f"hermes-down:{result.task.id}",
                payload_reference={"text": HERMES_DOWN_NOTICE})
        session.commit()
        # 200, а не 5xx: сообщение принято и сохранено, ретрай вебхука
        # ничего не улучшит — задачу подхватит восстановившийся Hermes.
        return {"status": "accepted_hermes_down", "task_id": str(result.task.id)}

    session.commit()
    response.status_code = status.HTTP_202_ACCEPTED
    return {"status": "accepted", "task_id": str(result.task.id)}
