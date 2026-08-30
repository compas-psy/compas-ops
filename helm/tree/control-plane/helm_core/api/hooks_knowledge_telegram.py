"""Dedicated Knowledge Bot вебхук (v3.8 §9.0, P8.6.2) — `/hooks/knowledge-telegram`.

Отдельный роутер от `hooks.py` (MAX/owner chief) намеренно: этот путь
НИКОГДА не вызывает Hermes/chief/approvals — по построению, не по
проверке в рантайме (модуль просто не импортирует ничего Hermes-
связанного). KNOWLEDGE_USER получает доступ ТОЛЬКО через verified
Telegram `from.id`, привязанный одноразовым инвайтом (`knowledge/
onboarding.py`) — owner chief bot и его `TELEGRAM_ALLOWED_USERS` этим
файлом не затронуты вовсе.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..channels.max import verify_webhook_secret
from ..channels.telegram_knowledge import InboundKnowledgeTelegram, WEBHOOK_SECRET_HEADER, parse_update
from ..knowledge.admin import try_admin_command
from ..knowledge.memory import try_remember
from ..knowledge.onboarding import consume_invite, find_user_by_identity, resolve_active_user_by_identity
from ..knowledge.probe import probe
from ..knowledge.tenancy import bind_knowledge_user
from ..outbox import enqueue
from .deps import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hooks", tags=["hooks"])

#: `knowledge_channel_identities.channel`/`knowledge_ingest_*.channel` и
#: т.д. — тот же словарь-по-строке, что "telegram"/"max" у owner-путей,
#: но ОТДЕЛЬНОЕ значение: одна и та же спека прямо требует не путать
#: identity secondary-пользователей с owner Telegram identity, даже если
#: оба технически "Telegram".
CHANNEL = "telegram_knowledge"

_START_COMMAND = "/start"
_INVITE_TOKEN_PREFIX = "kb_"

NO_ACCESS_NOTICE = "Нет доступа. Обратитесь к владельцу за приглашением."
SUSPENDED_NOTICE = "Доступ приостановлен. Обратитесь к владельцу."
ATTACHMENT_NOT_SUPPORTED_NOTICE = (
    "Файлы пока не поддерживаются в этом боте — только текст (в т.ч. «Запомни ...»)."
)
NEEDS_REASONING_NOTICE = (
    "Не нашёл ответа в сохранённом. Платный ИИ для этой роли по умолчанию выключен."
)
ONBOARDING_WELCOME = (
    "Готово! Это ваш личный Second Brain. Присылайте «Запомни ...», чтобы сохранить "
    "факт/ссылку/заметку, или спрашивайте о том, что уже сохранено."
)

_CONSUME_INVITE_NOTICES = {
    "invalid": "Ссылка недействительна.",
    "expired": "Ссылка просрочена — обратитесь к владельцу за новой.",
    "used": "Эта ссылка уже использована.",
    "revoked": "Ссылка отозвана.",
    "id_mismatch": "Эта ссылка предназначена другому Telegram-аккаунту.",
    "identity_already_bound": "Этот Telegram-аккаунт уже подключён к другому пользователю.",
}


def _reply(session: Session, inbound: InboundKnowledgeTelegram, text: str) -> None:
    enqueue(session, channel=CHANNEL, recipient=inbound.external_chat_id,
           reference=f"knowledge-telegram:{inbound.message_id or inbound.external_chat_id}",
           payload_reference={"text": text})


def _handle_start(session: Session, inbound: InboundKnowledgeTelegram) -> dict[str, Any]:
    parts = (inbound.text or "").split(maxsplit=1)
    token_arg = parts[1].strip() if len(parts) > 1 else ""
    if not token_arg.startswith(_INVITE_TOKEN_PREFIX):
        _reply(session, inbound, NO_ACCESS_NOTICE)
        session.commit()
        return {"status": "no_invite"}

    raw_token = token_arg[len(_INVITE_TOKEN_PREFIX):]
    outcome = consume_invite(session, raw_token=raw_token, channel=CHANNEL,
                             external_user_id=inbound.external_user_id,
                             external_chat_id=inbound.external_chat_id)
    _reply(session, inbound,
          ONBOARDING_WELCOME if outcome.status == "success" else _CONSUME_INVITE_NOTICES[outcome.status])
    session.commit()
    return {"status": f"invite_{outcome.status}"}


@router.post("/knowledge-telegram")
async def knowledge_telegram_webhook(request: Request,
                                     session: Session = Depends(get_session)) -> dict[str, Any]:
    if not verify_webhook_secret(request.app.state.knowledge_telegram_webhook_secret,
                                 request.headers.get(WEBHOOK_SECRET_HEADER)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "секрет вебхука не совпадает")

    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "тело не является JSON")
    if not isinstance(update, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "тело не является объектом")

    inbound = parse_update(update)
    if inbound is None or not inbound.is_private:
        # Не наш тип апдейта, либо групповой чат (§14.3 "private chat
        # only by default") — 200, чтобы Telegram не ретраил сознательно
        # игнорируемое.
        return {"status": "ignored"}

    if inbound.text and inbound.text.startswith(_START_COMMAND):
        # /start ДО проверки identity намеренно — это и есть момент,
        # когда identity ещё не существует, только создаётся.
        return _handle_start(session, inbound)

    # §14.16 "Target resolution is scoped to authenticated user only" —
    # identity резолвится ПЕРВОЙ для любого сообщения, не только для
    # тех, что содержат текст: неизвестный отправитель получает
    # одинаковый "нет доступа" независимо от содержимого.
    user = resolve_active_user_by_identity(session, channel=CHANNEL,
                                           external_user_id=inbound.external_user_id)
    if user is None:
        known_user = find_user_by_identity(session, channel=CHANNEL,
                                           external_user_id=inbound.external_user_id)
        notice = SUSPENDED_NOTICE if known_user is not None else NO_ACCESS_NOTICE
        _reply(session, inbound, notice)
        session.commit()
        return {"status": "suspended" if known_user is not None else "unknown_user"}

    bind_knowledge_user(session, user.id)

    if inbound.has_attachment:
        _reply(session, inbound, ATTACHMENT_NOT_SUPPORTED_NOTICE)
        session.commit()
        return {"status": "attachment_not_supported"}

    if not inbound.text:
        return {"status": "ignored"}

    remember_outcome = try_remember(session, channel=CHANNEL, text=inbound.text,
                                    origin_message_id=inbound.message_id,
                                    knowledge_user_id=user.id)
    if remember_outcome.status != "not_command":
        _reply(session, inbound, remember_outcome.text)
        session.commit()
        return {"status": f"remember_{remember_outcome.status}"}

    # §14.16: «Забудь …», «Верни в память …», «Удали навсегда …»,
    # «Исправь … : …». После «Запомни», потому что префиксы не
    # пересекаются («Не забудь» — это запомнить), и до обычного вопроса:
    # иначе «Забудь про код домофона» ушло бы в поиск и было бы понято
    # как просьба этот код НАЙТИ.
    admin_outcome = try_admin_command(session, text=inbound.text,
                                      knowledge_user_id=user.id)
    if admin_outcome.status != "not_command":
        _reply(session, inbound, admin_outcome.text)
        session.commit()
        return {"status": f"admin_{admin_outcome.status}"}

    # §14.11/9.0: тот же free-first Probe, что у SYSTEM_OWNER, с явным
    # knowledge_user_id этого secondary-пользователя — НЕ дефолт на
    # SYSTEM_OWNER (в отличие от owner-путей, где knowledge_user_id=None
    # разрешается в SYSTEM_OWNER по умолчанию).
    probe_result = probe(session, query=inbound.text, knowledge_user_id=user.id)
    if probe_result.outcome == "LOCAL_ANSWER":
        _reply(session, inbound, probe_result.answer_text)
        session.commit()
        return {"status": "local_answer"}

    # §14.18: "Knowledge-only users have no Hermes/OpenRouter/LiteLLM
    # credential path" — этот роутер не может вызвать Hermes, даже
    # ошибочно: он его попросту не импортирует. Честный отказ, не тихое
    # исчезновение сообщения.
    _reply(session, inbound, NEEDS_REASONING_NOTICE)
    session.commit()
    return {"status": "needs_reasoning_no_paid_ai"}
