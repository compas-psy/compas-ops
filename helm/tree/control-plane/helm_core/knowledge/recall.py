"""Micro-Memory recall — читающая половина «Запомни» (v3.8 §14.12-§14.13).

`memory.py` пишет память, этот модуль её достаёт. Разделены намеренно:
запись идёт по команде-префиксу и должна отвечать мгновенно, чтение
встроено в обычный `probe()` и происходит на КАЖДЫЙ вопрос — общего
состояния у них нет, кроме таблицы.

Ноль платного AI и ноль LLM вообще: детекция намерения — регулярки,
поиск — PostgreSQL FTS, композиция ответа — конкатенация. §14.14
"Protected exact data must never be rewritten" здесь выполняется не
проверкой, а конструкцией: переписывать текст нечем.

Осознанно упрощено против буквы спеки (см. V3.8-DELTA.md):
- §14.13 перечисляет семь намерений (RECALL_MEMORY/DOCUMENT_REQUEST/
  KNOWLEDGE_QUERY/KNOWLEDGE_ADMIN/REMINDER_TASK/LIVE_EXTERNAL/GENERAL).
  Реально меняет поведение сегодня ровно одно различие — «напомни» как
  постановка напоминания против «напомни» как вопрос к памяти: за
  DOCUMENT_REQUEST (§14.15) и KNOWLEDGE_ADMIN (§14.16) не стоит никакой
  реализации, LIVE_EXTERNAL не имеет коннектора. Классификатор на семь
  веток, пять из которых ведут в одно и то же место, — абстракция ради
  одного применения; здесь два предиката вместо enum'а, остальные
  намерения — явный, задокументированный пробел.
- §14.12 "deterministic rank fusion" между памятью и чанками документов
  не строится: попадание в память имеет АБСОЛЮТНЫЙ приоритет над
  документами. Спека требует "strong exact/recency/validity boost" —
  абсолютный приоритет и есть сильнейший из возможных boost'ов, при этом
  детерминированный и объяснимый, в отличие от весов, которые нечем
  откалибровать (golden-набора нет, §30.8.5).
- §14.13 "If ambiguous, ask one local clarification" — уточняющий диалог
  не реализован: он требует pending-состояния канала (как
  `chat_intake.py` для вложений). Вместо него детектор напоминания
  требует совпадения ТРЁХ признаков сразу, поэтому неоднозначное
  сообщение падает в recall — не деструктивный исход: подсистемы
  напоминаний в HELM нет вовсе, так что «не распознали напоминание»
  не теряет ничего, а «съели вопрос к памяти как напоминание» потеряло
  бы ответ.
- `KnowledgeMemory.last_used_at` recall'ом не обновляется — поле никто
  не читает, ранжирования по нему нет.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Text, func, or_, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.orm import Session

from ..models import KnowledgeMemory, KnowledgeMemoryStatus

#: Столько же, сколько документных чанков (`probe.MAX_EVIDENCE`) — у
#: перечисления Z1 нет причин быть длиннее для памяти, чем для
#: документов.
MAX_MEMORY_HITS = 5

#: «Напомни» — необходимый, но НЕ достаточный признак постановки
#: напоминания: «напомни мне номер машины курьера» — это вопрос к
#: памяти (§14.13 прямым текстом).
_REMINDER_LEAD_RE = re.compile(r"\bнапомн", re.IGNORECASE)

#: Явный будущий триггер (§14.13 "explicit future trigger/time/action").
_FUTURE_MARKER_RE = re.compile(
    r"(?:"
    r"\bсегодня\b"
    r"|\bзавтра\b"
    r"|\bпослезавтра\b"
    r"|через\s+\d+\s+(?:минут|час|дн|недел|месяц)"
    r"|\bв\s+\d{1,2}(?::\d{2})?\b"
    r"|\bв\s+(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\b"
    r")",
    re.IGNORECASE,
)

#: Инфинитив — то самое "action" из триггера: напоминают всегда СДЕЛАТЬ
#: что-то. Порог в три буквы до «-ть» отсекает существительные («путь»),
#: не отсекая коротких глаголов («купить»).
_INFINITIVE_RE = re.compile(r"\b[а-яё]{3,}ть(?:ся)?\b", re.IGNORECASE)

#: §14.12 "explicit historical query may include EXPIRED/SUPERSEDED".
_HISTORICAL_RE = re.compile(
    r"(?:\bбыл(?:а|о|и)?\b|\bвчера\b|\bпозавчера\b|\bраньше\b|\bпрежде\b|\bпрошл)",
    re.IGNORECASE,
)


def build_or_tsquery(query: str):
    """OR-версия `plainto_tsquery` — общая для памяти и документных
    чанков (`probe._lexical_search()`), поэтому живёт здесь, а не
    дублируется: AND-комбинация всех стемов вопроса не находит короткое
    фактическое утверждение, разделяющее с вопросом один-два стема."""
    raw_tsquery = func.plainto_tsquery("russian", query)
    return func.cast(func.replace(func.cast(raw_tsquery, Text), " & ", " | "), TSQUERY)


def is_future_reminder(text: str) -> bool:
    """«Напомни» + будущий момент + действие — все три сразу (§14.13:
    "`напомни` + explicit future trigger/time/action → task/reminder
    path"). Каждый признак по отдельности встречается и в законном
    вопросе к памяти: «напомни номер машины курьера, который приедет
    сегодня» — это recall, хотя в нём есть и «напомни», и «сегодня»."""
    return bool(
        _REMINDER_LEAD_RE.search(text)
        and _FUTURE_MARKER_RE.search(text)
        and _INFINITIVE_RE.search(text)
    )


def is_historical_query(text: str) -> bool:
    return _HISTORICAL_RE.search(text) is not None


@dataclass
class MemoryHit:
    memory_id: str
    canonical_text: str
    kind: str
    status: str
    expires_at: datetime | None
    rank: float


def search_memories(session: Session, *, query: str, knowledge_user_id: uuid.UUID,
                    now: datetime, include_historical: bool = False,
                    limit: int = MAX_MEMORY_HITS) -> list[MemoryHit]:
    """§14.10: "retrieval **also checks `expires_at` against current
    user-local time at query time** so a delayed routine cannot leak
    expired current context" — срок проверяется предикатом здесь, а не
    доверием к фоновой рутине, которая материализует `status=EXPIRED`
    (её в этой кодовой базе вообще нет: единственный механизм истечения
    — этот предикат)."""
    tsquery = build_or_tsquery(query)
    rank = func.ts_rank(KnowledgeMemory.tsv, tsquery, 2).label("rank")
    stmt = (
        select(KnowledgeMemory, rank)
        # v3.8 §14.4: knowledge_user_id — первый предикат. Explicit —
        # первый слой defense in depth, RLS — второй.
        .where(KnowledgeMemory.knowledge_user_id == knowledge_user_id)
        .where(KnowledgeMemory.tsv.op("@@")(tsquery))
        .order_by(rank.desc(), KnowledgeMemory.created_at.desc())
        .limit(limit)
    )
    if include_historical:
        # EXPIRED/SUPERSEDED — да ("retained for historical queries"),
        # DISABLED («Забудь») и DELETED — нет ни при каком режиме: явно
        # забытое не должно всплывать от формулировки вопроса.
        stmt = stmt.where(KnowledgeMemory.status.notin_(
            [KnowledgeMemoryStatus.DISABLED, KnowledgeMemoryStatus.DELETED]))
    else:
        stmt = stmt.where(KnowledgeMemory.status == KnowledgeMemoryStatus.ACTIVE).where(
            or_(KnowledgeMemory.expires_at.is_(None), KnowledgeMemory.expires_at > now))

    return [
        MemoryHit(memory_id=str(m.id), canonical_text=m.canonical_text, kind=m.kind,
                  status=m.status, expires_at=m.expires_at, rank=float(r))
        for m, r in session.execute(stmt).all()
    ]


def compose_memory_answer(hits: list[MemoryHit]) -> tuple[str, str]:
    """Единственное попадание возвращается ДОСЛОВНО, без обрамления —
    §14.14 "exact URL/identifier not modified": любая приписка вокруг
    ссылки делает ответ уже не байт-в-байт тем, что владелец сохранил."""
    if len(hits) == 1:
        return hits[0].canonical_text, "Z0"
    lines = ["Из памяти:"]
    lines.extend(f"{i}. {hit.canonical_text}" for i, hit in enumerate(hits, 1))
    return "\n".join(lines), "Z1"
