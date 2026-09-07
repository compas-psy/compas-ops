"""Из чего этому чату разрешено отвечать — и почему это лежит в базе.

ДЕФЕКТ (разбор владельца 07.09.2026). Право на платный переход
передавалось от пользовательского входа: `probe(paid_allowed=...)`.
Telegram-плагин вычислял его как `not in_memory_conversation`, а
`in_memory_conversation` читал из внутрипроцессного словаря последних
ходов. Перезапуск шлюза стирал словарь — и разговор, целиком
состоявший из вопросов к собственным записям, после рестарта снова
получал право уйти в платную модель.

Формулировка владельца точная: «Отсутствие контекста не является
разрешением». Отсюда два свойства этого модуля.

ПЕРВОЕ: умолчание закрыто. Нет строки — режим `memory`. Не «неизвестно,
спросим модель», не «раз не запрещено, значит можно»: незнание не может
быть разрешением тратить деньги (устав §6, CLAUDE.md §5.2).

ВТОРОЕ: режим переживает перезапуск. Он в базе, а не в памяти процесса,
потому что свойство «этот чат отвечает только из моей памяти» обязано
жить столько же, сколько сама память.

ЧЕГО ЗДЕСЬ НЕТ НАМЕРЕННО. Ни одного слова-признака, по которому вопрос
считался бы личным или общим. Владелец запретил чинить это словарём
прямо, и правильно: прогон 422 показал, что «что я беру с собой из
лекарств?» словарь относит к общим вопросам. Режим переключается
командой, а не угадывается по формулировке.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import KnowledgeChatMode, KnowledgeChatModeValue
from .tenancy import bind_knowledge_user

#: Режим чата, о котором ничего не известно.
DEFAULT_MODE = KnowledgeChatModeValue.MEMORY

#: Явное разрешение и явный запрет. Обе формулировки — команды, а не
#: признаки: они опознаются целиком и не пытаются угадать намерение в
#: обычной фразе. Поэтому якоря на начало и конец сообщения.
_PAID_ON_RE = re.compile(
    r"^\s*(?:разреши(?:ть)?\s+платн\w+(?:\s+\w+)?|можно\s+платн\w+|"
    r"платный\s+режим(?:\s+вкл\w*)?)\s*[.!]?\s*$", re.IGNORECASE)
_PAID_OFF_RE = re.compile(
    r"^\s*(?:запрети(?:ть)?\s+платн\w+(?:\s+\w+)?|только\s+(?:моя\s+)?память|"
    r"только\s+из\s+памяти|платный\s+режим\s+выкл\w*)\s*[.!]?\s*$", re.IGNORECASE)

MODE_SET_TEXT = {
    KnowledgeChatModeValue.PAID:
        "Платные ответы в этом чате разрешены. Вопросы, на которые в ваших "
        "записях ничего нет, будут уходить в платную модель. Выключить — "
        "«только память».",
    KnowledgeChatModeValue.MEMORY:
        "Отвечаю только из ваших записей. Платная модель не вызывается.",
}


def detect_mode_command(text: str) -> KnowledgeChatModeValue | None:
    """Явное переключение режима, если сообщение это оно и есть."""
    if _PAID_ON_RE.match(text or ""):
        return KnowledgeChatModeValue.PAID
    if _PAID_OFF_RE.match(text or ""):
        return KnowledgeChatModeValue.MEMORY
    return None


def _row(session: Session, *, channel: str, chat_id: str,
         knowledge_user_id: uuid.UUID) -> KnowledgeChatMode | None:
    return session.scalar(
        select(KnowledgeChatMode).where(
            KnowledgeChatMode.knowledge_user_id == knowledge_user_id,
            KnowledgeChatMode.channel == channel,
            KnowledgeChatMode.chat_id == chat_id))


def get_mode(session: Session, *, channel: str, chat_id: str,
             knowledge_user_id: uuid.UUID | None = None) -> KnowledgeChatModeValue:
    """Режим чата. Нет строки — `MEMORY`, и это не заглушка, а политика."""
    # Тенант разрешается ЗДЕСЬ, а не берётся как есть: строка с NULL в
    # `knowledge_user_id` не проходит RLS-предикат (равенство с NULL —
    # не истина), то есть режим записался бы и стал невидим. Тот же
    # порядок, что у probe() и всех прочих tenant-scoped функций.
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)
    row = _row(session, channel=channel, chat_id=chat_id,
               knowledge_user_id=knowledge_user_id)
    return KnowledgeChatModeValue(row.mode) if row is not None else DEFAULT_MODE


def set_mode(session: Session, *, channel: str, chat_id: str,
             mode: KnowledgeChatModeValue,
             knowledge_user_id: uuid.UUID | None = None) -> None:
    """Переключить режим чата. Только по явной команде владельца."""
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)
    row = _row(session, channel=channel, chat_id=chat_id,
               knowledge_user_id=knowledge_user_id)
    if row is None:
        session.add(KnowledgeChatMode(
            id=uuid.uuid4(), knowledge_user_id=knowledge_user_id,
            channel=channel, chat_id=chat_id, mode=mode))
    else:
        row.mode = mode
    session.flush()


def paid_allowed_for(session: Session, *, channel: str | None, chat_id: str | None,
                     knowledge_user_id: uuid.UUID | None = None) -> bool:
    """Разрешён ли платный переход этому входу.

    Чат не назван — платный переход закрыт. Вход, который не может
    сказать, кто спрашивает, тем более не может разрешить трату.
    """
    if not channel or not chat_id:
        return False
    return get_mode(session, channel=channel, chat_id=chat_id,
                    knowledge_user_id=knowledge_user_id) == KnowledgeChatModeValue.PAID
