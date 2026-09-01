"""Free-first Knowledge Probe (ТЗ §14.11-§14.13).

Pre-LLM gate: вызывается ДО диспетчеризации к Hermes (Telegram —
`helm-control`, MAX — `/hooks/max`), не «совет RAG поискать». Каждый
обычный вопрос владельца проходит через это ДО платной модели.

Гибридный поиск (ADR-025, §14.12 "FTS + pgvector"): лексический слой
(`ts_rank` на `tsvector`) остаётся первым и приоритетным — он уже
откалиброван на реальном использовании (`MIN_RANK_SCORE`). pgvector
дополняет его результатами, которых лексика не видит вовсе
(перефразировка без общих словных корней с источником) — не заменяет и
не переупорядочивает то, что лексика уже нашла. Рано или поздно
`MIN_COSINE_SIMILARITY` потребует такой же калибровки на реальном
использовании, какую уже прошёл `MIN_RANK_SCORE` — сегодня это первая
прикидка по минимальному живому замеру (см. ADR-025), не финальное
число.

Обнаружение противоречий (§14.13: «no unresolved contradiction») здесь
НЕ реализовано: оно требует заполненного knowledge_relations, а ничто
пока не создаёт туда записи (P8.5.2 — экстракция связей при ingest, тоже
отложена). Известный пробел, не молчаливый — несколько найденных чанков
показываются как есть, без утверждения, что они согласуются.

Z2-рефраз (§14.12, docs/KNOWLEDGE_MODELS.md) — `rephrase.py`, локальный
Ollama, только для Z0: перефразирует единственную найденную цитату в
более естественный тон персональным стилем владельца (`style.py`).
Fail-open — недоступность Ollama не роняет Probe, Z0-текст уходит как
есть. mode остаётся "Z0" в обоих случаях (рефраз не создаёт новый
уровень ответа, это пост-обработка уже найденного).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .embeddings import embed_texts_or_none
from .health_schema import health_schema_configured, health_session
from .recall import (
    MemoryHit, build_or_tsquery, compose_memory_answer, is_future_reminder,
    is_historical_query, search_memories,
)
from .rephrase import rephrase_or_none
from .tenancy import bind_knowledge_user
from ..models import (
    HealthKnowledgeChunk, HealthKnowledgeSourcePrivate, KnowledgeAnswerRun, KnowledgeChunk,
    KnowledgeDomain, KnowledgeSource, KnowledgeStatus,
)
from ..models.base import utcnow

#: §14.13 требует «calibrated threshold» — реального golden-набора
#: (§30.8.5) ещё нет, P8.5.2 не сделан, поэтому это первая прикидка, не
#: финальная калибровка. Значение измерено напрямую в psql на
#: ts_rank(..., normalization=2): шумовое совпадение по одному случайному
#: слову даёт ~0.0009, реальные совпадения из тестового корпуса — 0.0068–
#: 0.0203. Порог 0.003 лежит чисто между ними. Пересмотреть на первом
#: реальном golden-set, не раньше.
MIN_RANK_SCORE = 0.003

#: НАЙДЕНО 01.09.2026 (реальный чат владельца): "каких врачей я посещал"
#: вернул 5 совпадений "Врач КДЛ:" — подпись лаборанта на бланке анализа,
#: попадающая в СВОЙ отдельный чанк (несколько слов), а не реальные
#: посещения врача. ts_rank(normalization=2) делит ранг на длину
#: документа — короткий чанк, целиком состоящий из совпавшего слова,
#: получает завышенный ранг вне зависимости от того, несёт ли он вообще
#: какую-то информацию. Раз лексика с приоритетом закрывает MAX_EVIDENCE
#: (ADR-025 docstring выше), 5 одинаковых пустых фрагментов не оставляли
#: pgvector ни единого шанса найти реальные "ОСМОТР ГАСТРОЭНТЕРОЛОГА"/
#: "Врач уролог: Кириченко..." (подтверждённый живым замером cosine
#: 0.67-0.71). Порог — не про качество совпадения, а про то, есть ли в
#: чанке вообще что процитировать: "Врач КДЛ:" (9 символов) непригоден
#: как Z0/Z1-цитата независимо от ранга.
MIN_LEXICAL_CHUNK_CHARS = 20

#: Верхних чанков берём — и для Z1-перечисления, и (в будущем) для
#: evidence pack, уходящего в Hermes при NEEDS_REASONING.
MAX_EVIDENCE = 5

#: ADR-025: первая прикидка, не откалиброванный порог (нет golden-set —
#: тот же статус, что MIN_RANK_SCORE до своего первого реального
#: замера). Измерено на минимальной проверке смысла (embed_benchmark.py,
#: 31.08.2026): у выбранной модели (MiniLM-L12-v2) отвлекающий текст на
#: другую тему даёт похожесть от -0.04 до 0.01, реальные "перефразировка
#: без общих корней" — 0.20-0.55. Порог взят с запасом над потолком шума.
MIN_COSINE_SIMILARITY = 0.35


@dataclass
class Evidence:
    chunk_id: str
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
    #: Заполнено вместо `evidence`, когда ответ пришёл из Micro-Memory —
    #: память и документные чанки не смешиваются в одном ответе.
    memory: list[MemoryHit] = field(default_factory=list)


def query_hash(query: str) -> str:
    """Публичная: переиспользуется вызывающим кодом (например, `/hooks/max`)
    при логировании `knowledge_answer_runs` для NEEDS_REASONING→C1 —
    один и тот же вопрос обязан хэшироваться одинаково независимо от
    того, кто пишет строку."""
    return hashlib.sha256(query.strip().casefold().encode("utf-8")).hexdigest()


def _lexical_search(session: Session, *, query: str, domain: str | None,
                    knowledge_user_id: uuid.UUID) -> list[Evidence]:
    # plainto_tsquery AND-combines all stems ('как' & 'решен' & 'приня') —
    # a natural-language question then matches only a document containing
    # ALL of its stems. Real documents here are single factual statements
    # sharing just one stem with the question, so OR-ify: any stem present
    # is enough to surface as a candidate; ts_rank still ranks documents
    # matching more of the query's terms higher (confirmed live via psql).
    tsquery = build_or_tsquery(query)
    # normalization=2 divides rank by document length — without it a long,
    # mostly-irrelevant document with one coincidental keyword match scores
    # identically to a short, genuinely relevant one (confirmed via psql).
    rank = func.ts_rank(KnowledgeChunk.tsv, tsquery, 2).label("rank")
    stmt = (
        select(KnowledgeChunk, KnowledgeSource, rank)
        .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
        .where(KnowledgeChunk.tsv.op("@@")(tsquery))
        .where(func.length(KnowledgeChunk.text) >= MIN_LEXICAL_CHUNK_CHARS)
        .where(KnowledgeSource.status != KnowledgeStatus.ARCHIVED)
        # v3.8 §14.4 query rule: knowledge_user_id — первый предикат, не
        # последний штрих. Explicit-предикат здесь — первый слой defense
        # in depth, RLS (FORCE ROW LEVEL SECURITY) — второй; ни один не
        # заменяет другой.
        .where(KnowledgeSource.knowledge_user_id == knowledge_user_id)
        .order_by(rank.desc())
        .limit(MAX_EVIDENCE)
    )
    # Решение владельца 01.09.2026: все домены, включая health, отвечают
    # в общем бесплатном поиске — второй мозг не имеет смысла, если
    # владелец обязан помнить явный синтаксис домена для собственных же
    # данных. Единственное оставшееся исключение — simpas/zapiski,
    # клиентский контент чужих людей: спека прямо требует "not indexed
    # into general namespaces" (P8.5.7, chat_intake.py форсирует
    # client_restricted при ingest) — это защита приватности КЛИЕНТА, не
    # организационное неудобство, поэтому не отменяется вместе с health.
    stmt = (stmt.where(KnowledgeSource.domain == domain) if domain is not None
           else stmt.where(KnowledgeSource.domain != KnowledgeDomain.SIMPAS_ZAPISKI))

    rows = session.execute(stmt).all()
    return [
        Evidence(chunk_id=str(chunk.id), source_id=str(src.id), chunk_text=chunk.text,
                original_filename=src.original_filename, rank=float(r))
        for chunk, src, r in rows
    ]


def _vector_search(session: Session, *, query_embedding: list[float], domain: str | None,
                   knowledge_user_id: uuid.UUID, exclude_chunk_ids: set[str]) -> list[Evidence]:
    """ADR-025: та же тенантная/доменная фильтрация, что `_lexical_search`,
    но по косинусному расстоянию, а не `tsv`. `exclude_chunk_ids` — чанки,
    уже найденные лексически, не дублируются здесь (лексика приоритетнее,
    см. docstring модуля)."""
    similarity = (1 - KnowledgeChunk.embedding.cosine_distance(query_embedding)).label("similarity")
    stmt = (
        select(KnowledgeChunk, KnowledgeSource, similarity)
        .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
        .where(KnowledgeChunk.embedding.isnot(None))
        .where(KnowledgeSource.status != KnowledgeStatus.ARCHIVED)
        .where(KnowledgeSource.knowledge_user_id == knowledge_user_id)
        .order_by(similarity.desc())
        .limit(MAX_EVIDENCE)
    )
    # То же исключение simpas/zapiski, что уже применяет _lexical_search
    # (см. её комментарий) — health в общем поиске участвует наравне со
    # всеми остальными доменами.
    stmt = (stmt.where(KnowledgeSource.domain == domain) if domain is not None
           else stmt.where(KnowledgeSource.domain != KnowledgeDomain.SIMPAS_ZAPISKI))

    rows = session.execute(stmt).all()
    return [
        Evidence(chunk_id=str(chunk.id), source_id=str(src.id), chunk_text=chunk.text,
                original_filename=src.original_filename, rank=float(sim))
        for chunk, src, sim in rows
        if str(chunk.id) not in exclude_chunk_ids and float(sim) >= MIN_COSINE_SIMILARITY
    ]


def _health_lexical_search(*, query: str, knowledge_user_id: uuid.UUID) -> list[Evidence]:
    """Тот же лексический поиск, что `_lexical_search`, на health-
    соединении (ADR-005/P12) — `health.knowledge_chunks` физически не
    видна `helm_app`, обычная сессия здесь не годится вообще. Вызывается
    и на общий вопрос (`domain=None`), и на явный `domain="health"` —
    решение владельца 01.09.2026, health больше не исключение."""
    tsquery = build_or_tsquery(query)
    rank = func.ts_rank(HealthKnowledgeChunk.tsv, tsquery, 2).label("rank")
    with health_session(knowledge_user_id) as session:
        stmt = (
            select(HealthKnowledgeChunk, HealthKnowledgeSourcePrivate.original_filename, rank)
            .outerjoin(HealthKnowledgeSourcePrivate,
                      HealthKnowledgeChunk.source_id == HealthKnowledgeSourcePrivate.source_id)
            .where(HealthKnowledgeChunk.tsv.op("@@")(tsquery))
            .where(func.length(HealthKnowledgeChunk.text) >= MIN_LEXICAL_CHUNK_CHARS)
            .where(HealthKnowledgeChunk.knowledge_user_id == knowledge_user_id)
            .order_by(rank.desc())
            .limit(MAX_EVIDENCE)
        )
        rows = session.execute(stmt).all()
        return [
            Evidence(chunk_id=str(chunk.id), source_id=str(chunk.source_id), chunk_text=chunk.text,
                    original_filename=filename, rank=float(r))
            for chunk, filename, r in rows
        ]


def _health_vector_search(*, query_embedding: list[float], knowledge_user_id: uuid.UUID,
                          exclude_chunk_ids: set[str]) -> list[Evidence]:
    """Health-эквивалент `_vector_search()` — см. её docstring."""
    similarity = (1 - HealthKnowledgeChunk.embedding.cosine_distance(query_embedding)).label("similarity")
    with health_session(knowledge_user_id) as session:
        stmt = (
            select(HealthKnowledgeChunk, HealthKnowledgeSourcePrivate.original_filename, similarity)
            .outerjoin(HealthKnowledgeSourcePrivate,
                      HealthKnowledgeChunk.source_id == HealthKnowledgeSourcePrivate.source_id)
            .where(HealthKnowledgeChunk.embedding.isnot(None))
            .where(HealthKnowledgeChunk.knowledge_user_id == knowledge_user_id)
            .order_by(similarity.desc())
            .limit(MAX_EVIDENCE)
        )
        rows = session.execute(stmt).all()
        return [
            Evidence(chunk_id=str(chunk.id), source_id=str(chunk.source_id), chunk_text=chunk.text,
                    original_filename=filename, rank=float(sim))
            for chunk, filename, sim in rows
            if str(chunk.id) not in exclude_chunk_ids and float(sim) >= MIN_COSINE_SIMILARITY
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


def probe(session: Session, *, query: str, domain: str | None = None,
         knowledge_user_id: uuid.UUID | None = None) -> ProbeResult:
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

    `knowledge_user_id=None` — существующие call sites (P8.6.2 Dedicated
    Knowledge Bot ещё не существует): разрешается в SYSTEM_OWNER. v3.8
    §14.4 "every query starts with knowledge_user_id" — до этого захода
    `_lexical_search()` не фильтровала по тенанту вообще; сейчас
    единственный тенант делает это неотличимым от прежнего поведения, но
    закрывает реальную дыру до того, как появится второй пользователь.
    """
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)

    # §14.13: «напомни» + явный будущий триггер + действие — это
    # постановка напоминания, а не вопрос к памяти. Подсистемы задач/
    # напоминаний в HELM нет вообще, поэтому единственная честная форма
    # «маршрутизации в REMINDER_TASK» — не отвечать из памяти и
    # эскалировать: у SYSTEM_OWNER это дойдёт до chief, у KNOWLEDGE_USER
    # — до честного отказа Dedicated Bot'а (§14.13 "never route such
    # request into Hermes by accident" для secondary соблюдается тем,
    # что этот бот в Hermes не ходит вовсе).
    if is_future_reminder(query):
        return ProbeResult(outcome="NEEDS_REASONING")

    # §14.12 unified retrieval: память проверяется ДО документных чанков
    # и имеет над ними абсолютный приоритет (осознанное упрощение
    # "strong exact boost", см. recall.py). Фильтр по домену к памяти не
    # применяется: §14.10 "retrieval remains global so this never hides
    # memory", и `domain` у memory-записей сегодня всегда NULL.
    memory_hits = [
        hit for hit in search_memories(
            session, query=query, knowledge_user_id=knowledge_user_id, now=utcnow(),
            include_historical=is_historical_query(query))
        if hit.rank >= MIN_RANK_SCORE
    ]
    if memory_hits:
        answer_text, mode = compose_memory_answer(memory_hits)
        session.add(KnowledgeAnswerRun(
            knowledge_user_id=knowledge_user_id,
            query_hash=query_hash(query), domain=domain, mode=mode,
            paid_ai_used=False, evidence_count=len(memory_hits),
        ))
        return ProbeResult(outcome="LOCAL_ANSWER", mode=mode, answer_text=answer_text,
                           memory=memory_hits)

    # ADR-005/P12 + решение владельца 01.09.2026: health участвует в
    # общем бесплатном поиске наравне со всеми доменами — единственное,
    # что решает явный domain="health", это ГДЕ физически лежат чанки
    # (после scripts/setup-health-role.sh — в health.knowledge_chunks,
    # обычная сессия их больше не видит), не ДОПУСК к ним. Поэтому общий
    # вопрос (domain=None) при настроенной схеме обязан заглянуть в обе
    # схемы и объединить находки, а явный domain="health" — только в
    # health-схему (там же лежит вся история, дублировать public не
    # нужно). Без настроенной схемы health ещё физически в public —
    # public-путь его и так находит (см. _lexical_search).
    search_health = domain in (None, KnowledgeDomain.HEALTH) and health_schema_configured()
    search_public = domain != KnowledgeDomain.HEALTH or not health_schema_configured()

    lexical_hits: list[Evidence] = []
    if search_public:
        lexical_hits += _lexical_search(session, query=query, domain=domain,
                                        knowledge_user_id=knowledge_user_id)
    if search_health:
        lexical_hits += _health_lexical_search(query=query, knowledge_user_id=knowledge_user_id)
    evidence = sorted((e for e in lexical_hits if e.rank >= MIN_RANK_SCORE),
                      key=lambda e: e.rank, reverse=True)[:MAX_EVIDENCE]

    # ADR-025: pgvector дополняет лексику местами, до MAX_EVIDENCE — не
    # запрашивается вовсе, если лексика уже набрала полный колчан
    # (экономит HTTP-вызов к embed-сервису на самом частом случае, когда
    # обычный лексический поиск и так справился). Fail-open: недоступный
    # embed-сервис — это НЕ повод эскалировать вопрос, который лексика
    # уже покрыла бы сама; для чисто-перефразированных вопросов без
    # лексических совпадений это просто означает эскалацию, как и было
    # до ADR-025 — деградация до прежнего поведения, не новый отказ.
    if len(evidence) < MAX_EVIDENCE:
        query_embedding = embed_texts_or_none([query])[0]
        if query_embedding is not None:
            exclude_ids = {e.chunk_id for e in evidence}
            vector_hits: list[Evidence] = []
            if search_public:
                vector_hits += _vector_search(
                    session, query_embedding=query_embedding, domain=domain,
                    knowledge_user_id=knowledge_user_id, exclude_chunk_ids=exclude_ids,
                )
            if search_health:
                vector_hits += _health_vector_search(
                    query_embedding=query_embedding, knowledge_user_id=knowledge_user_id,
                    exclude_chunk_ids=exclude_ids,
                )
            evidence = (evidence + vector_hits)[:MAX_EVIDENCE]

    # §14.13 quality gate: без evidence выше порога — сразу эскалация, а
    # не «уверенный бесплатный ответ ради экономии».
    if not evidence:
        return ProbeResult(outcome="NEEDS_REASONING")

    answer_text, mode = _compose_answer(evidence)

    # §14.12 Z2-рефраз (docs/KNOWLEDGE_MODELS.md, gemma2:2b выбран живым
    # замером 31.08.2026) — ТОЛЬКО для Z0 (одна цитата). Замер проверял
    # рефраз ровно одного факта за раз; Z1 (пронумерованный список
    # нескольких находок) рефразом не покрыт — совмещать несколько
    # разных фактов в одном вызове модели непроверено и рискованнее
    # (больше риск подмешать одну находку в формулировку другой), это
    # сознательно нетронутая, а не забытая часть. paid_ai_used не
    # трогается ни в одной ветке — локальный Ollama-рефраз не платный
    # вызов (§14.14).
    if mode == "Z0":
        rephrased = rephrase_or_none(
            session, question=query, evidence_text=evidence[0].chunk_text,
            knowledge_user_id=knowledge_user_id,
        )
        if rephrased is not None:
            cite = evidence[0].original_filename or evidence[0].source_id
            answer_text = f"{rephrased}\n\nИсточник: {cite}"

    session.add(KnowledgeAnswerRun(
        knowledge_user_id=knowledge_user_id,
        query_hash=query_hash(query), domain=domain, mode=mode,
        paid_ai_used=False, evidence_count=len(evidence),
    ))
    return ProbeResult(outcome="LOCAL_ANSWER", mode=mode, answer_text=answer_text,
                       evidence=evidence)
