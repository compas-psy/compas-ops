"""Free-first Knowledge Probe (ТЗ §14.11-§14.13).

Pre-LLM gate: вызывается ДО диспетчеризации к Hermes (Telegram —
`helm-control`, MAX — `/hooks/max`), не «совет RAG поискать». Каждый
обычный вопрос владельца проходит через это ДО платной модели.

Только лексический слой в этом заходе (без embeddings/rank fusion/
rerank — ждут выбора модели бенчмарком, V3.4-DELTA.md). Это означает:
probe находит меньше, чем финальная версия (семантическая перефразировка
без общих слов с источником пока не найдётся), но то, что находит —
находит бесплатно, детерминированно и с проверяемым provenance, что и
есть суть §14.11, а не полнота покрытия.

Обнаружение противоречий (§14.13: «no unresolved contradiction») здесь
НЕ реализовано: оно требует заполненного knowledge_relations, а ничто
пока не создаёт туда записи (P8.5.2 — экстракция связей при ingest, тоже
отложена). Известный пробел, не молчаливый — несколько найденных чанков
показываются как есть, без утверждения, что они согласуются.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import Text, func, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.orm import Session

from ..models import KnowledgeAnswerRun, KnowledgeChunk, KnowledgeDomain, KnowledgeSource, KnowledgeStatus

#: §14.13 требует «calibrated threshold» — реального golden-набора
#: (§30.8.5) ещё нет, P8.5.2 не сделан, поэтому это первая прикидка, не
#: финальная калибровка. Значение измерено напрямую в psql на
#: ts_rank(..., normalization=2): шумовое совпадение по одному случайному
#: слову даёт ~0.0009, реальные совпадения из тестового корпуса — 0.0068–
#: 0.0203. Порог 0.003 лежит чисто между ними. Пересмотреть на первом
#: реальном golden-set, не раньше.
MIN_RANK_SCORE = 0.003

#: Верхних чанков берём — и для Z1-перечисления, и (в будущем) для
#: evidence pack, уходящего в Hermes при NEEDS_REASONING.
MAX_EVIDENCE = 5


@dataclass
class Evidence:
    source_id: str
    chunk_text: str
    original_filename: str | None
    rank: float


@dataclass
class ProbeResult:
    outcome: Literal["LOCAL_ANSWER", "NEEDS_REASONING"]
    #: Z0 | Z1, заполнено только при outcome == LOCAL_ANSWER.
    mode: str | None = None
    answer_text: str | None = None
    evidence: list[Evidence] = field(default_factory=list)


def query_hash(query: str) -> str:
    """Публичная: переиспользуется вызывающим кодом (например, `/hooks/max`)
    при логировании `knowledge_answer_runs` для NEEDS_REASONING→C1 —
    один и тот же вопрос обязан хэшироваться одинаково независимо от
    того, кто пишет строку."""
    return hashlib.sha256(query.strip().casefold().encode("utf-8")).hexdigest()


def _lexical_search(session: Session, *, query: str, domain: str | None) -> list[Evidence]:
    # plainto_tsquery AND-combines all stems ('как' & 'решен' & 'приня') —
    # a natural-language question then matches only a document containing
    # ALL of its stems. Real documents here are single factual statements
    # sharing just one stem with the question, so OR-ify: any stem present
    # is enough to surface as a candidate; ts_rank still ranks documents
    # matching more of the query's terms higher (confirmed live via psql).
    raw_tsquery = func.plainto_tsquery("russian", query)
    tsquery = func.cast(func.replace(func.cast(raw_tsquery, Text), " & ", " | "), TSQUERY)
    # normalization=2 divides rank by document length — without it a long,
    # mostly-irrelevant document with one coincidental keyword match scores
    # identically to a short, genuinely relevant one (confirmed via psql).
    rank = func.ts_rank(KnowledgeChunk.tsv, tsquery, 2).label("rank")
    stmt = (
        select(KnowledgeChunk, KnowledgeSource, rank)
        .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
        .where(KnowledgeChunk.tsv.op("@@")(tsquery))
        .where(KnowledgeSource.status != KnowledgeStatus.ARCHIVED)
        .order_by(rank.desc())
        .limit(MAX_EVIDENCE)
    )
    # §14.15: health и simpas/zapiski не входят в обычный поиск по
    # умолчанию. health — chief не получает raw health RAG на общий
    # вопрос, только на явный health-scope (reviewer temporary explicit
    # scope — отдельный механизм, не эта функция). simpas/zapiski —
    # клиентский контент, спека прямо требует "not indexed into general
    # namespaces" (P8.5.7, chat_intake.py форсирует client_restricted при
    # ingest) — общий вопрос не должен случайно процитировать заметку о
    # клиенте, только явный domain="simpas/zapiski".
    stmt = (stmt.where(KnowledgeSource.domain == domain) if domain is not None
           else stmt.where(KnowledgeSource.domain.notin_(
               [KnowledgeDomain.HEALTH, KnowledgeDomain.SIMPAS_ZAPISKI])))

    rows = session.execute(stmt).all()
    return [
        Evidence(source_id=str(src.id), chunk_text=chunk.text,
                original_filename=src.original_filename, rank=float(r))
        for chunk, src, r in rows
    ]


def _compose_answer(evidence: list[Evidence]) -> tuple[str, str]:
    """Детерминированный composer (§14.12) — без LLM."""
    if len(evidence) == 1:
        cite = evidence[0].original_filename or evidence[0].source_id
        return f"{evidence[0].chunk_text}\n\nИсточник: {cite}", "Z0"
    lines = [f"Найдено {len(evidence)} совпадений:"]
    for i, e in enumerate(evidence, 1):
        cite = e.original_filename or e.source_id
        lines.append(f"{i}. {e.chunk_text} (источник: {cite})")
    return "\n".join(lines), "Z1"


def probe(session: Session, *, query: str, domain: str | None = None) -> ProbeResult:
    """Прогнать вопрос через локальную базу знаний до платной модели.

    LOCAL_ANSWER пишет строку `knowledge_answer_runs` сразу — paid_ai_used
    заведомо False, остальных полей достаточно для paid-avoidance метрики
    (§14.14). NEEDS_REASONING строку НЕ пишет: mode (C1 или неудавшийся
    Z2) и cloud_model станут известны только после реального вызова
    Hermes — логировать эту строку обязан вызывающий код после ответа.
    `/hooks/max` делает это (§10.2, in-process — Control Plane сам вызывает
    Hermes и видит ответ). `helm-control` (Telegram) — нет: Hermes вызывает
    LLM у себя, Control Plane не видит момент завершения хода, чтобы
    залогировать строку постфактум; это открытый пробел, не реализовано,
    ждёт живой разведки хуков gateway на предмет пост-ответного события.
    """
    evidence = _lexical_search(session, query=query, domain=domain)

    # §14.13 quality gate: без evidence выше порога — сразу эскалация, а
    # не «уверенный бесплатный ответ ради экономии».
    evidence = [e for e in evidence if e.rank >= MIN_RANK_SCORE]
    if not evidence:
        return ProbeResult(outcome="NEEDS_REASONING")

    answer_text, mode = _compose_answer(evidence)
    session.add(KnowledgeAnswerRun(
        query_hash=query_hash(query), domain=domain, mode=mode,
        paid_ai_used=False, evidence_count=len(evidence),
    ))
    return ProbeResult(outcome="LOCAL_ANSWER", mode=mode, answer_text=answer_text,
                       evidence=evidence)
