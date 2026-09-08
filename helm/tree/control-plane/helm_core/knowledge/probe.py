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
from datetime import date
from itertools import zip_longest
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .answer_format import (PERSONAL_NOT_FOUND, format_doctors, format_missing_comparison_side,
                            format_nearest_quote, format_nothing_answered,
                            format_unknown_source, format_unverified,
                            format_with_sources, is_quotable)
from .embeddings import embed_texts_or_none
from .health_schema import health_schema_configured, health_session
from .operations import (FORM_MISSING_NOTICE, OP_COMPARE, OP_COUNT, OP_ENUMERATE,
                         OP_EXAMPLE, OP_VALUE, claims_example,
                         missing_comparison_side, run_count, run_enumerate,
                         select_for_operation)
from .query_router import QuestionIntent, answer_doctors_visited, detect_intent
from .query_spec import MODE_GENERAL, DialogueContext, build_query_spec
from .recall import (
    MemoryHit, build_or_tsquery, compose_memory_answer, is_future_reminder,
    is_historical_query, search_memories,
)
from .rephrase import rephrase_or_none
from .synthesis import Synthesis, synthesize_or_none
from .temporal import fact_date
from .documents import detect_document_request, document_reply
from ..config import get_settings
from .tenancy import bind_knowledge_user
from ..models import (
    HealthKnowledgeChunk, HealthKnowledgeSourcePrivate, KnowledgeAnswerMode, KnowledgeAnswerRun,
    KnowledgeChunk, KnowledgeDomain, KnowledgeMemory, KnowledgeMemoryStatus,
    KnowledgeSource, KnowledgeStatus,
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

#: Ранжирование ЧАНКОВ. Отдельно от `MIN_RANK_SCORE` с 06.09.2026: до
#: этого одно число обслуживало и чанки, и записи «Запомни», а это
#: разные тексты, ранжируемые по-разному. Память — короткие фразы, у
#: них деление ранга на длину осмысленно и порог 0.003 под них
#: откалиброван; трогать его, не измерив память, нельзя.
#:
#: ЧТО СЛОМАЛОСЬ. Перечанковка 06.09.2026 подняла медиану чанка с 65
#: символов до 288. `normalization=2` делит ранг на длину — ранги
#: уехали под порог, и лексика замолчала: замер (прогон 373) дал 0–1
#: попадание выше порога на восьми вопросах, включая четыре, ответ на
#: которые в корпусе есть. Отвечал один вектор, а различать «про это» и
#: «не про это» на разнице 0.008 он не может.
#:
#: Разбор 05.09 предсказывал это дословно: «Даже с правильными чанками
#: ts_rank(normalization=2) продолжит поднимать короткое. Это стоит
#: пересмотреть вместе с перечанковкой, а не отдельно».
#:
#: ЧТО ИЗМЕРЕНО (прогон 378, три нормализации на одних вопросах):
#:
#:               norm=0     norm=1     norm=2
#:   ответ есть  0.03040    0.00571    0.00078   давление
#:               0.06301    0.00939    0.00163   общий анализ крови
#:               0.05300    0.00744    0.00101   УЗИ брюшной полости
#:               0.04559    0.00939    0.00405   кардиолог в заключении
#:   ответа нет  0.00000    0.00000    0.00000   три несвязанные темы
#:               0.01216    0.00253    0.00045   марка бетона
#:
#: Разделяют все три, но `norm=0` даёт самый широкий зазор (0.012 против
#: 0.030) и не зависит от длины чанка — то есть переживёт следующую
#: смену нарезки, в отличие от предшественника. Длину чанка ранг больше
#: не штрафует: строки бланка, ради которых штраф вводился, убраны
#: перечанковкой, и лечить их ранжированием больше не нужно.
CHUNK_RANK_NORMALIZATION = 0

#: ПОРОГА РАНГА У ЧАНКОВ БОЛЬШЕ НЕТ. Снят 06.09.2026, после замера,
#: который опроверг собственное обоснование предыдущей константы
#: (`MIN_CHUNK_RANK_SCORE = 0.02`, взятая как середина зазора между
#: шумом 0.01216 и слабейшим настоящим ответом 0.03040).
#:
#: Замер: три коротких источника «Решение №N: используем X» и два
#: вопроса о них.
#:
#:   «какое решение приняли»                    ранг 0.02026 — проходил
#:   «какие решения приняли по инфраструктуре»   ранг 0.01520 — НЕ проходил
#:
#: Тот же документ, то же совпадение, ранг ниже — потому что
#: `ts_rank` считает долю совпавших лексем ОТ ВСЕГО ЗАПРОСА. Абсолютный
#: порог на этой величине наказывает за длину вопроса: чем подробнее
#: спрошено, тем вероятнее ответ отброшен. Зазор, померенный на
#: конкретных вопросах разведки 378, на вопросы другой длины не
#: переносится — это и была ошибка той калибровки.
#:
#: Что теперь отделяет ответ от шума: не число, а прочтение. Кандидатов
#: даёт поиск (`@@`, длина чанка, `is_quotable`), а решает, отвечают ли
#: они на вопрос, ступень синтеза (synthesis.py) — она читает найденное
#: и вправе сказать «ответа здесь нет». Распоряжение владельца
#: 06.09.2026: «Не превращай совпадение слов в обязательное условие
#: ответа… Возможность ответить определяется содержанием доказательств
#: и их соответствием вопросу».

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

#: СКОЛЬКО ФРАГМЕНТОВ ВИДИТ МОДЕЛЬ — бюджет её контекста, и только он.
#:
#: Это НЕ мера того, сколько знаний у системы есть (распоряжение
#: владельца 07.09.2026, п.2: «MAX_EVIDENCE=5 не может определять
#: полноту знаний, доступных исполнителю»). Пять — предел, при котором
#: gemma2:2b на CPU ещё отвечает за отведённые 45 секунд, то есть
#: свойство железа и модели, а не корпуса.
#:
#: Полный набор найденного живёт рядом (`ProbeResult.candidates`) и
#: нужен операциям, которым пятёрки мало по существу: «сколько»
#: считает по всему подходящему набору, «все» обязано его обойти
#: целиком либо честно сказать, что показало не всё.
MAX_EVIDENCE = 5

#: Сколько кандидатов ЗАПРАШИВАЕТСЯ у поиска до отбора. Больше, чем
#: уходит в ответ, и вот почему: на «уровень холестерина по липидному
#: профилю» несколько документов совпадают одинаково (все содержат и
#: «липид», и «холестерин»), Postgres отдаёт первые пять из них в
#: произвольном порядке, и нужный — сам липидный профиль — в пятёрку не
#: попадал (живой прогон 402). Отбор среди равных по рангу делается
#: здесь, по дате, а не отдаётся случаю.
CANDIDATE_LIMIT = MAX_EVIDENCE * 3

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
    #: Дата, которой датирован документ. `None` — определить не удалось;
    #: ответ обязан сказать это, а не подставить дату загрузки.
    content_date: date | None = None
    #: Дата самих сведений во фрагменте, если она в нём написана:
    #: пересказ чужого анализа несёт свою дату рядом с собой. Отличается
    #: от даты документа — и именно эта разница решает, что «последнее».
    fact_date: date | None = None


@dataclass
class ProbeResult:
    #: LOCAL_NOT_FOUND добавлен 06.09.2026: вопрос о данных владельца,
    #: ответа в памяти нет — и это НЕ повод платить. Отличается от
    #: NEEDS_REASONING именно правом на эскалацию, а не текстом.
    outcome: Literal["LOCAL_ANSWER", "LOCAL_NOT_FOUND",
                     "NEEDS_CLARIFICATION", "NEEDS_REASONING"]
    #: Z0 | Z1, заполнено только при outcome == LOCAL_ANSWER.
    mode: str | None = None
    answer_text: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    #: ВЕСЬ найденный и пригодный набор, а не только показанное модели.
    #: Разделено 07.09.2026 по п.2 распоряжения: бюджет контекста и
    #: полнота знаний — разные величины, и складывать их в одну значило
    #: бы объявлять «система знает пять фрагментов». Операции подсчёта и
    #: перечисления (п.3) считают по нему.
    candidates: list["Evidence"] = field(default_factory=list)
    #: Заполнено вместо `evidence`, когда ответ пришёл из Micro-Memory —
    #: память и документные чанки не смешиваются в одном ответе.
    memory: list[MemoryHit] = field(default_factory=list)
    #: Источники в ОДНОЙ форме для всех режимов. `evidence` — чанки,
    #: `proofs` структурного пути — спаны; вызывающему нужен один список,
    #: который можно показать пользователю. До 06.09.2026 структурный
    #: ответ терял доказательства целиком: `format_doctors()` их не
    #: печатает, а `ProbeResult` не выносил наружу — ответ приходил без
    #: единой ссылки, хотя спаны были посчитаны.
    sources: list[dict[str, Any]] = field(default_factory=list)
    #: id строки `knowledge_answer_runs`. Нужен, чтобы сцепить ответ,
    #: который увидел пользователь, с серверным следом: без него
    #: «ответ пришёл» и «ответ записан бесплатным» — два независимых
    #: утверждения, и проверить их совпадение нечем.
    answer_run_id: str | None = None


def query_hash(query: str) -> str:
    """Публичная: переиспользуется вызывающим кодом (например, `/hooks/max`)
    при логировании `knowledge_answer_runs` для NEEDS_REASONING→C1 —
    один и тот же вопрос обязан хэшироваться одинаково независимо от
    того, кто пишет строку."""
    return hashlib.sha256(query.strip().casefold().encode("utf-8")).hexdigest()


def _apply_domain_filter(stmt, domain: str | None):
    """Доменный фильтр public-пути, общий для лексики и векторов.

    Решение владельца 01.09.2026: все домены, включая health, отвечают в
    общем бесплатном поиске — второй мозг не имеет смысла, если владелец
    обязан помнить явный синтаксис домена для собственных же данных.

    Из общего поиска исключены ровно два случая, и по разным причинам:

    `simpas/zapiski` — клиентский контент чужих людей, спека прямо
    требует "not indexed into general namespaces" (P8.5.7,
    `chat_intake.py` форсирует `client_restricted` при ingest). Это
    защита приватности КЛИЕНТА, а не организационное неудобство.

    `health` при настроенной health-схеме — не про доступ, а про то,
    чтобы не ответить дважды одним и тем же. Общий вопрос ходит в обе
    схемы (`probe()`), а во время миграции R1 один и тот же чанк
    физически лежит и в `public`, и в `health`: без этого исключения он
    занял бы два слота из пяти. Когда health-схема не настроена,
    health-текст живёт только в public — и тогда исключать его нельзя,
    иначе корпус погаснет.
    """
    if domain is not None:
        return stmt.where(KnowledgeSource.domain == domain)
    excluded = [KnowledgeDomain.SIMPAS_ZAPISKI]
    if health_schema_configured():
        excluded.append(KnowledgeDomain.HEALTH)
    return stmt.where(KnowledgeSource.domain.notin_(excluded))


def _apply_source_filter(stmt, chunk_model, source_ids: tuple[str, ...]):
    """Разговор назвал документ — искать в нём, а не по всему корпусу
    (P4, «что там прописал врач?» после ответа по конкретному приёму).
    Пустой кортеж — обычный поиск везде."""
    if not source_ids:
        return stmt
    return stmt.where(chunk_model.source_id.in_([uuid.UUID(s) for s in source_ids]))


def _exclude_forgotten(stmt):
    """Источник, заведённый из записи памяти, отвечает ровно пока сама
    запись активна.

    Гарантия ставится в ПОИСКЕ, а не в обработчике «Забудь это»: статус
    записи меняется не только командой (истечение срока, правка,
    будущий код), и забытое не должно всплывать из-за того, что
    кто-то поменял поле мимо одного конкретного места. Команда «Забудь»
    вдобавок archive-ит источник — это про панель и выдачу оригинала,
    здесь про ответы.
    """
    forgotten = (select(KnowledgeMemory.source_id)
                 .where(KnowledgeMemory.source_id.is_not(None),
                        KnowledgeMemory.status != KnowledgeMemoryStatus.ACTIVE))
    return stmt.where(KnowledgeSource.id.notin_(forgotten))


def _lexical_search(session: Session, *, query: str, domain: str | None,
                    knowledge_user_id: uuid.UUID,
                    source_ids: tuple[str, ...] = ()) -> list[Evidence]:
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
    rank = func.ts_rank(KnowledgeChunk.tsv, tsquery, CHUNK_RANK_NORMALIZATION).label("rank")
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
        .limit(CANDIDATE_LIMIT)
    )
    stmt = _exclude_forgotten(_apply_source_filter(_apply_domain_filter(stmt, domain),
                                                   KnowledgeChunk, source_ids))

    rows = session.execute(stmt).all()
    return [
        Evidence(chunk_id=str(chunk.id), source_id=str(src.id), chunk_text=chunk.text,
                original_filename=src.original_filename, rank=float(r))
        for chunk, src, r in rows
    ]


def _vector_search(session: Session, *, query_embedding: list[float], domain: str | None,
                   knowledge_user_id: uuid.UUID, exclude_chunk_ids: set[str],
                   source_ids: tuple[str, ...] = ()) -> list[Evidence]:
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
        # ОТСЕВ ДО LIMIT, А НЕ ПОСЛЕ. Порог близости и уже найденное
        # лексикой раньше проверялись в Python, то есть по строкам,
        # которые SQL уже отобрал и обрезал. Пять мест уходили на
        # кандидатов, часть которых тут же выбрасывалась, и место
        # оставалось пустым — дозаполнить его было уже нечем.
        .where(similarity >= MIN_COSINE_SIMILARITY)
        .where(KnowledgeChunk.id.notin_([uuid.UUID(cid) for cid in exclude_chunk_ids]))
        .order_by(similarity.desc())
        .limit(CANDIDATE_LIMIT)
    )
    stmt = _exclude_forgotten(_apply_source_filter(_apply_domain_filter(stmt, domain),
                                                   KnowledgeChunk, source_ids))

    return [
        Evidence(chunk_id=str(chunk.id), source_id=str(src.id), chunk_text=chunk.text,
                original_filename=src.original_filename, rank=float(sim))
        for chunk, src, sim in session.execute(stmt).all()
    ]


#: По скольким первым фрагментам документ «представляется». Название и
#: автор стоят в начале: у fb2 — строкой «# Название» и «Автор: …», у
#: PDF — шапкой бланка. Дальше по тексту то же имя встречается в
#: ссылках и цитатах, и считать это представлением нельзя: книга,
#: цитирующая Фрейда, книгой Фрейда не становится.
SOURCE_TITLE_CHUNKS = 2


def _like(hint: str) -> str:
    """Подстрока для ILIKE. `%` и `_` из вопроса экранируются: иначе
    название с процентом стало бы шаблоном по всему корпусу."""
    escaped = hint.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _sources_named(session: Session, *, hint: str, domain: str | None,
                   knowledge_user_id: uuid.UUID) -> tuple[str, ...]:
    """Источники, которые САМИ СЕБЯ так называют — по имени файла или по
    началу текста.

    Не по всему тексту: иначе «по книге Линде» нашло бы каждый документ,
    где Линде упомянут. Не по словарю названий: его пришлось бы вести
    руками, и он устаревал бы с каждой загрузкой.
    """
    like = _like(hint)
    found: set[str] = set()
    search_health = domain in (None, KnowledgeDomain.HEALTH) and health_schema_configured()
    search_public = domain != KnowledgeDomain.HEALTH or not health_schema_configured()

    if search_public:
        found |= {str(sid) for sid in session.scalars(
            select(KnowledgeSource.id).where(
                KnowledgeSource.knowledge_user_id == knowledge_user_id,
                KnowledgeSource.original_filename.ilike(like, escape="\\"))).all()}
        found |= {str(sid) for sid in session.scalars(
            select(KnowledgeChunk.source_id).where(
                KnowledgeChunk.knowledge_user_id == knowledge_user_id,
                KnowledgeChunk.ordinal < SOURCE_TITLE_CHUNKS,
                KnowledgeChunk.text.ilike(like, escape="\\"))).all()}

    if search_health:
        with health_session(knowledge_user_id) as health:
            found |= {str(sid) for sid in health.scalars(
                select(HealthKnowledgeSourcePrivate.source_id).where(
                    HealthKnowledgeSourcePrivate.knowledge_user_id == knowledge_user_id,
                    HealthKnowledgeSourcePrivate.original_filename.ilike(
                        like, escape="\\"))).all()}
            found |= {str(sid) for sid in health.scalars(
                select(HealthKnowledgeChunk.source_id).where(
                    HealthKnowledgeChunk.knowledge_user_id == knowledge_user_id,
                    HealthKnowledgeChunk.ordinal < SOURCE_TITLE_CHUNKS,
                    HealthKnowledgeChunk.text.ilike(like, escape="\\"))).all()}

    return tuple(sorted(found))


def _health_lexical_search(*, query: str, knowledge_user_id: uuid.UUID,
                           source_ids: tuple[str, ...] = ()) -> list[Evidence]:
    """Тот же лексический поиск, что `_lexical_search`, на health-
    соединении (ADR-005/P12) — `health.knowledge_chunks` физически не
    видна `helm_app`, обычная сессия здесь не годится вообще. Вызывается
    и на общий вопрос (`domain=None`), и на явный `domain="health"` —
    решение владельца 01.09.2026, health больше не исключение."""
    tsquery = build_or_tsquery(query)
    rank = func.ts_rank(HealthKnowledgeChunk.tsv, tsquery,
                        CHUNK_RANK_NORMALIZATION).label("rank")
    with health_session(knowledge_user_id) as session:
        stmt = (
            select(HealthKnowledgeChunk, HealthKnowledgeSourcePrivate.original_filename, rank)
            .outerjoin(HealthKnowledgeSourcePrivate,
                      HealthKnowledgeChunk.source_id == HealthKnowledgeSourcePrivate.source_id)
            .where(HealthKnowledgeChunk.tsv.op("@@")(tsquery))
            .where(func.length(HealthKnowledgeChunk.text) >= MIN_LEXICAL_CHUNK_CHARS)
            .where(HealthKnowledgeChunk.knowledge_user_id == knowledge_user_id)
            .order_by(rank.desc())
            .limit(CANDIDATE_LIMIT)
        )
        stmt = _apply_source_filter(stmt, HealthKnowledgeChunk, source_ids)
        rows = session.execute(stmt).all()
        return [
            Evidence(chunk_id=str(chunk.id), source_id=str(chunk.source_id), chunk_text=chunk.text,
                    original_filename=filename, rank=float(r))
            for chunk, filename, r in rows
        ]


def _health_vector_search(*, query_embedding: list[float], knowledge_user_id: uuid.UUID,
                          exclude_chunk_ids: set[str],
                          source_ids: tuple[str, ...] = ()) -> list[Evidence]:
    """Health-эквивалент `_vector_search()` — см. её docstring."""
    similarity = (1 - HealthKnowledgeChunk.embedding.cosine_distance(query_embedding)).label("similarity")
    with health_session(knowledge_user_id) as session:
        stmt = (
            select(HealthKnowledgeChunk, HealthKnowledgeSourcePrivate.original_filename, similarity)
            .outerjoin(HealthKnowledgeSourcePrivate,
                      HealthKnowledgeChunk.source_id == HealthKnowledgeSourcePrivate.source_id)
            .where(HealthKnowledgeChunk.embedding.isnot(None))
            .where(HealthKnowledgeChunk.knowledge_user_id == knowledge_user_id)
            # Тот же отсев до LIMIT, что в `_vector_search()`.
            .where(similarity >= MIN_COSINE_SIMILARITY)
            .where(HealthKnowledgeChunk.id.notin_(
                [uuid.UUID(cid) for cid in exclude_chunk_ids]))
            .order_by(similarity.desc())
            .limit(CANDIDATE_LIMIT)
        )
        stmt = _apply_source_filter(stmt, HealthKnowledgeChunk, source_ids)
        return [
            Evidence(chunk_id=str(chunk.id), source_id=str(chunk.source_id), chunk_text=chunk.text,
                    original_filename=filename, rank=float(sim))
            for chunk, filename, sim in session.execute(stmt).all()
        ]


#: Напоминание, которое некому поставить, и платить за него нельзя.
#: Подсистемы напоминаний в HELM нет вовсе (§14.13) — честная форма
#: отказа, а не молчание и не оплаченная догадка.
REMINDER_NOT_SUPPORTED = (
    "Напоминания я пока не ставлю — такой подсистемы в HELM нет. "
    "Если это нужно запомнить, начните сообщение с «Запомни»."
)


def _local_dead_end(session: Session, *, query: str, domain: str | None,
                    knowledge_user_id: uuid.UUID) -> ProbeResult:
    """Тупик, из которого раньше выходили платным вызовом.

    Запрос к памяти не покупает ответ ни при какой причине остановки
    (распоряжение владельца 07.09.2026, п.5). Строка `knowledge_answer_
    runs` пишется здесь так же, как у обычного N0: отказ — это тоже
    ответ, и он должен быть виден в метрике paid-avoidance."""
    run_id = uuid.uuid4()
    session.add(KnowledgeAnswerRun(
        id=run_id, knowledge_user_id=knowledge_user_id,
        query_hash=query_hash(query), domain=domain,
        mode=KnowledgeAnswerMode.N0, paid_ai_used=False, evidence_count=0,
    ))
    return ProbeResult(outcome="LOCAL_NOT_FOUND", mode=KnowledgeAnswerMode.N0,
                       answer_text=REMINDER_NOT_SUPPORTED, answer_run_id=str(run_id))


def _merge_branches(lexical: list[Evidence], vector: list[Evidence]) -> list[Evidence]:
    """Слить находки двух веток честной чересполосицей.

    Ранг `ts_rank` и косинусная близость — величины из разных шкал, и
    складывать их значило бы придумать курс обмена, которого нет.
    Поэтому сравниваются не веса, а МЕСТА: первый лексический, первый
    векторный, второй лексический, второй векторный. Каждая ветка
    получает свою долю пятёрки, и ни одна не может вытеснить другую
    целиком — ровно то, чего не хватало.

    Кто идёт первым, решает лексика — по тому, различила ли она хоть
    что-нибудь. Если её лучший кандидат ранжирован ровно так же, как
    худший, значит совпало одно общее слово на всех, и порядок внутри
    ветки случаен; вести должен вектор. Признак не пороговый, а
    относительный: абсолютный порог здесь невозможен, `ts_rank` зависит
    от числа лексем в вопросе (найдено разбором 06.09.2026, из-за чего
    прежний `MIN_CHUNK_RANK_SCORE` и был убран).

    Пересечения между ветками нет по построению: вектор ищет с
    `exclude_chunk_ids` по всему, что рассмотрела лексика.
    """
    lexical_discriminates = bool(lexical) and lexical[0].rank > lexical[-1].rank
    first, second = ((lexical, vector) if lexical_discriminates else (vector, lexical))
    merged: list[Evidence] = []
    for pair in zip_longest(first, second):
        merged += [item for item in pair if item is not None]
    return merged


def _freshness(item: Evidence) -> date:
    """Чем датируется фрагмент, когда вопрос про «последний раз»:
    сведениями внутри него, иначе документом, иначе ничем — и тогда он
    не может считаться свежим. Неизвестную дату нельзя объявить самой
    новой: это было бы утверждением, которого у нас нет."""
    return item.fact_date or item.content_date or date.min


def _tiebreak_freshness(item: Evidence) -> date:
    """То же, но для РАЗВЕДЕНИЯ РАВНЫХ ПО РАНГУ, и с обратным
    умолчанием.

    Вопросы разные, поэтому и умолчания разные. «Что новее» — если даты
    нет, свежим назвать нельзя. «Которое из неразличимых показать» —
    если даты нет, старым назвать тоже нельзя, а `date.min` именно это и
    делал: гнал в конец очереди всё недатированное.

    Замером (прогон 422) видно, чего это стоило. На «до какого числа
    действует загран» лексика вернула одиннадцать кандидатов с ОДНИМ И
    ТЕМ ЖЕ рангом 0.01520 — собственная запись владельца среди них.
    Ранги равны, решала дата, у записи её нет — и запись ушла в хвост,
    уступив пятёрку медицинским PDF. Владелец получил «не нашёл» про то,
    что сам продиктовал часом раньше.
    """
    return item.fact_date or item.content_date or date.max


def _attach_dates(session: Session, items: list[Evidence]) -> None:
    """Проставить датам фрагментов их значения одним запросом на всех.

    Health-чанки лежат в своей схеме, но строка источника — общая,
    поэтому запрос один и тот же. Повторный вызов на уже размеченных
    фрагментах ничего не портит: значения те же.
    """
    if not items:
        return
    dates = dict(session.execute(
        select(KnowledgeSource.id, KnowledgeSource.content_date)
        .where(KnowledgeSource.id.in_([uuid.UUID(item.source_id) for item in items]))).all())
    for item in items:
        item.content_date = dates.get(uuid.UUID(item.source_id))
        # Дата сведений — из текста самого фрагмента. Консультация от
        # 25.08 может пересказывать анализ от 07.10.2023, и для
        # «последнего» такой фрагмент старый, а не свежий.
        item.fact_date = fact_date(item.chunk_text)


def _source_label(item: Evidence) -> str:
    """Имя источника с его датой — чтобы владелец видел, к какому числу
    относится ответ, не открывая документ."""
    name = item.original_filename or item.source_id
    if item.content_date is None:
        return name
    return f"{name} ({item.content_date:%d.%m.%Y})"


def _dated_fragment(item: Evidence) -> str:
    """Фрагмент для синтеза, подписанный датой документа.

    Без даты в самом фрагменте модель физически не может ни выбрать
    последний результат, ни сказать, к какому числу относится значение
    — а именно этого не хватило в живом ответе владельцу 07.09.2026.
    """
    if item.fact_date is not None and item.fact_date != item.content_date:
        # Сведения старше своего документа: он их пересказывает.
        # Модель обязана видеть обе даты, иначе выберет по обложке.
        document = (f", документ от {item.content_date:%d.%m.%Y}"
                    if item.content_date else "")
        return f"(данные от {item.fact_date:%d.%m.%Y}{document}) {item.chunk_text}"
    if item.content_date is None:
        return f"(дата документа неизвестна) {item.chunk_text}"
    return f"(документ от {item.content_date:%d.%m.%Y}) {item.chunk_text}"


def _compose_answer(evidence: list[Evidence]) -> tuple[str, str]:
    """Детерминированный composer (§14.12) — без LLM.

    Ветка «несколько совпадений» больше не перечисляет их. Замер
    production 05.09.2026: все десять бесплатных ответов за всё время
    были именно этим списком ровно из пяти сырых фрагментов, и именно он
    читается как «отвечает много и не по делу»
    (docs/PRODUCTION_ANSWERS_RCA_2026-09-05.md §1.1). Контракт владельца
    от 05.09.2026 запрещает такой вывод прямо; отдаётся один ближайший
    фрагмент, честно названный ближайшим, а не ответом. Режим остаётся
    Z1 — уровень ответа тот же, изменился способ его сказать.
    """
    cite = evidence[0].original_filename or evidence[0].source_id
    if len(evidence) == 1:
        return f"{evidence[0].chunk_text}\n\nИсточник: {cite}", KnowledgeAnswerMode.Z0
    return format_nearest_quote(evidence[0].chunk_text, cite), KnowledgeAnswerMode.Z1


def probe(session: Session, *, query: str, domain: str | None = None,
         knowledge_user_id: uuid.UUID | None = None,
         context: DialogueContext | None = None,
         paid_allowed: bool = False) -> ProbeResult:
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

    `paid_allowed=False` ПО УМОЛЧАНИЮ, и это не мелочь настройки, а
    политика (распоряжение владельца 07.09.2026, п.5): «У запроса к
    памяти должна быть обязательная политика local-only, передаваемая от
    пользовательского входа через весь маршрут. Неизвестное намерение,
    пустой поиск, ошибка разбора и таймаут не разрешают платный вызов».

    Раньше право на платный переход выводилось из ФОРМУЛИРОВКИ вопроса:
    `classify_mode()` смотрел, похож ли текст на обращение к записям, и
    непохожий пропускал к платной модели. Прогон 422 показал цену:
    «что я беру с собой из лекарств?» и «в каком порядке я всё делаю по
    прилёте?» — вопросы к собственным записям владельца — были
    определены как general и получили право на оплату. Чинить это
    добавлением слов «беру» и «прилёт» в регулярное выражение владелец
    запретил прямо, и правильно: словарь никогда не догонит живую речь.

    Теперь право приходит СВЕРХУ, от того, кто знает режим задачи:
    вызывающий обязан назвать его явно. Умолчание закрыто — незнание не
    может быть разрешением. Платные инженерные задачи это не трогает:
    их маршрут передаёт `paid_allowed=True` сам.

    `knowledge_user_id=None` — существующие call sites (P8.6.2 Dedicated
    Knowledge Bot ещё не существует): разрешается в SYSTEM_OWNER. v3.8
    §14.4 "every query starts with knowledge_user_id" — до этого захода
    `_lexical_search()` не фильтровала по тенанту вообще; сейчас
    единственный тенант делает это неотличимым от прежнего поведения, но
    закрывает реальную дыру до того, как появится второй пользователь.
    """
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)

    # P4: вопрос разбирается ОДИН раз и целиком, до всякого поиска.
    # Раньше его свойства выяснялись по дороге в разных местах — тенант
    # здесь, год внутри исполнителя врачей, область данных перед
    # решением об оплате, неприменимый период в форматтере. Ни одно не
    # существовало как факт, о котором можно спросить, и ответ не мог
    # честно перечислить, что применил, а что нет.
    spec = build_query_spec(query, tenant_id=knowledge_user_id, context=context)

    # §14.15 В БОТЕ: «отдай сам файл» — просьба о документе, а не вопрос
    # о его содержании, и отвечать на неё пересказом значит не ответить.
    #
    # Скриншоты владельца 07.09.2026: на «отдай сам pdf последнего
    # клинического анализа крови» приходил текст, на «отдай сам файл, а
    # не текст» — «не нашёл». Выдача оригинала существовала только в
    # веб-панели: `documents.py` импортировал один `api/panel.py`.
    #
    # Проверяется РАНЬШЕ поиска: искать по такому вопросу нечего, его
    # предмет — файл. Документ, не названный прямо, берётся из прошлого
    # хода разговора — ровно так владелец и спрашивал, следом за ответом.
    document_subject = detect_document_request(query)
    if document_subject is not None:
        run_id = uuid.uuid4()
        session.add(KnowledgeAnswerRun(
            id=run_id, knowledge_user_id=knowledge_user_id,
            query_hash=query_hash(query), domain=domain,
            mode=KnowledgeAnswerMode.Z2, paid_ai_used=False, evidence_count=0,
        ))
        return ProbeResult(
            outcome="LOCAL_ANSWER", mode=KnowledgeAnswerMode.Z2,
            answer_text=document_reply(
                session, subject=document_subject,
                knowledge_user_id=knowledge_user_id,
                panel_origin=get_settings().panel_origin,
                fallback_source_ids=tuple(context.source_ids) if context else ()),
            answer_run_id=str(run_id))

    # Уточнение — не ошибка и не пустой ответ, а третий исход. «Что там
    # прописал врач?» не имеет ответа сам по себе: «там» указывает на
    # документ из разговора, а памяти разговора у probe нет. До сих пор
    # система отвечала на такой вопрос ближайшим похожим текстом, то
    # есть угадывала, о чём речь.
    if spec.clarification:
        run_id = uuid.uuid4()
        session.add(KnowledgeAnswerRun(
            id=run_id, knowledge_user_id=knowledge_user_id,
            query_hash=query_hash(query), domain=domain,
            mode=KnowledgeAnswerMode.Q0, paid_ai_used=False, evidence_count=0,
        ))
        return ProbeResult(outcome="NEEDS_CLARIFICATION", mode=KnowledgeAnswerMode.Q0,
                           answer_text=spec.clarification, answer_run_id=str(run_id))

    # §14.13: «напомни» + явный будущий триггер + действие — это
    # постановка напоминания, а не вопрос к памяти. Подсистемы задач/
    # напоминаний в HELM нет вообще, поэтому единственная честная форма
    # «маршрутизации в REMINDER_TASK» — не отвечать из памяти и
    # эскалировать: у SYSTEM_OWNER это дойдёт до chief, у KNOWLEDGE_USER
    # — до честного отказа Dedicated Bot'а (§14.13 "never route such
    # request into Hermes by accident" для secondary соблюдается тем,
    # что этот бот в Hermes не ходит вовсе).
    if is_future_reminder(query):
        if paid_allowed:
            return ProbeResult(outcome="NEEDS_REASONING")
        return _local_dead_end(session, query=query, domain=domain,
                               knowledge_user_id=knowledge_user_id)

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
    # ЗАМЕТКА ОТДАЁТСЯ ЦЕЛИКОМ ТОЛЬКО ТАМ, ГДЕ ЦЕЛИКОМ И ПРОСИЛИ.
    #
    # Распоряжение владельца 07.09.2026, п.2: «Убери безусловный ранний
    # возврат всей быстрой заметки. Её совпадение должно участвовать в
    # общем ответе с учётом запрошенной детализации».
    #
    # «С учётом детализации» — это и есть операция. На вопрос о значении
    # заметка и есть ответ, и отдаётся дословно: байтовая точность
    # сохранённого («Ссылка на ваш канал B17: …») — принятое владельцем
    # поведение, и терять её нельзя. А на «сколько» и «все» дословный
    # текст ответом не является: там нужен пересчёт и обход набора,
    # и заметка идёт в общий набор кандидатов наравне с документами.
    if memory_hits and spec.operation == OP_VALUE:
        answer_text, mode = compose_memory_answer(memory_hits)
        run_id = uuid.uuid4()
        session.add(KnowledgeAnswerRun(
            id=run_id,
            knowledge_user_id=knowledge_user_id,
            query_hash=query_hash(query), domain=domain, mode=mode,
            paid_ai_used=False, evidence_count=len(memory_hits),
        ))
        return ProbeResult(outcome="LOCAL_ANSWER", mode=mode, answer_text=answer_text,
                           memory=memory_hits, answer_run_id=str(run_id),
                           # `source_id` — чтобы ответ из памяти можно было
                           # ОТКРЫТЬ: с 06.09.2026 у записи есть источник, и
                           # без этого поля панель показывала бы ответ без
                           # единой кнопки подтверждения.
                           sources=[{"kind": "memory", "memory_id": str(hit.memory_id),
                                     "source_id": hit.source_id}
                                    for hit in memory_hits])

    # Структурный вопрос отвечается по доказанному (R5-R7), а не поиском
    # похожего текста. Это место — то самое, где найденная 05.09.2026
    # первая точка поломки закрывается: до этой правки распознавания
    # намерения в production не было вовсе, и «каких врачей я посещал»
    # шло тем же кодом, что «что было в анализе 12 марта».
    #
    # Отказ здесь окончателен и НЕ эскалируется: если намерение
    # распознано, а доказанного ответа нет, платная модель истории
    # владельца всё равно не знает и заполнит пустоту общими
    # рассуждениями — ровно то, что запрещает контракт ответа.
    if detect_intent(query) == QuestionIntent.DOCTORS_VISITED:
        structured = answer_doctors_visited(session, question=query,
                                            knowledge_user_id=knowledge_user_id)
        # Одно доказательство — один источник, названный тем, что он
        # есть: путь графа даёт ребро, путь доказательств — спан. Что
        # именно, решает сам `Proof`, а не это место: раньше решало
        # здесь, и решало неверно на всём живом корпусе.
        structured_sources = [proof.as_source()
                              for item in structured.items for proof in item.proofs]
        run_id = uuid.uuid4()
        session.add(KnowledgeAnswerRun(
            id=run_id,
            knowledge_user_id=knowledge_user_id,
            query_hash=query_hash(query), domain=domain, mode=KnowledgeAnswerMode.S1,
            paid_ai_used=False, evidence_count=len(structured.items),
        ))
        return ProbeResult(outcome="LOCAL_ANSWER", mode=KnowledgeAnswerMode.S1,
                           answer_text=format_doctors(structured),
                           answer_run_id=str(run_id), sources=structured_sources)

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

    # ИСТОЧНИК, НАЗВАННЫЙ В САМОМ ВОПРОСЕ (п.3 распоряжения 07.09.2026:
    # контракт запроса обязан доходить до исполнителя). Ограничение по
    # документу в системе было, но заполнялось только из контекста
    # разговора — «по книге Линде что такое ЭОТ?» условием не считалось
    # вовсе и получило в ответ случайную главу той же книги.
    #
    # Разговор имеет приоритет: он указывает на конкретные документы
    # прошлого ответа, а название из вопроса ещё нужно разрешить.
    focus_source_ids = spec.focus_source_ids
    if spec.source_hint and not focus_source_ids:
        focus_source_ids = _sources_named(session, hint=spec.source_hint, domain=domain,
                                          knowledge_user_id=knowledge_user_id)
        if not focus_source_ids:
            run_id = uuid.uuid4()
            session.add(KnowledgeAnswerRun(
                id=run_id, knowledge_user_id=knowledge_user_id,
                query_hash=query_hash(query), domain=domain,
                mode=KnowledgeAnswerMode.N0, paid_ai_used=False, evidence_count=0,
            ))
            return ProbeResult(outcome="LOCAL_NOT_FOUND", mode=KnowledgeAnswerMode.N0,
                               answer_text=format_unknown_source(spec.source_hint),
                               answer_run_id=str(run_id))

    # ПОЛНОТА РЕШАЕТСЯ ЗДЕСЬ, ДО ВСЯКОЙ ФИЛЬТРАЦИИ.
    #
    # Разбор владельца 07.09.2026: «полноту нельзя определять только
    # числом кандидатов после фильтрации». Раньше `run_enumerate` брал
    # `complete=len(candidates) < CANDIDATE_LIMIT`, где `candidates` —
    # то, что осталось ПОСЛЕ отбраковки `is_quotable` и переупорядочивания.
    # Набор, обрезанный запросом на пятнадцатой записи, но похудевший
    # отбраковкой до девяти, выглядел полным — и «все» становилось
    # утверждением, которого у нас нет.
    #
    # Честный признак один: упёрлась ли хоть одна ветка поиска в свой
    # LIMIT. Упёрлась — значит за границей могло остаться ещё.
    branch_sizes: list[int] = []

    def _branch(hits: list) -> list:
        branch_sizes.append(len(hits))
        return hits

    lexical_hits: list[Evidence] = []
    if search_public:
        lexical_hits += _branch(_lexical_search(
            session, query=spec.retrieval_question, domain=domain,
            knowledge_user_id=knowledge_user_id, source_ids=focus_source_ids))
    if search_health:
        lexical_hits += _branch(_health_lexical_search(
            query=spec.retrieval_question, knowledge_user_id=knowledge_user_id,
            source_ids=focus_source_ids))
    lexical = sorted(lexical_hits, key=lambda e: e.rank, reverse=True)
    # ОТБРАКОВКА ЛЕКСИКИ ДО РЕШЕНИЯ «КОЛЧАН ПОЛОН». Переставлено
    # 06.09.2026 по живому прогону 365: на «что там прописал врач?»
    # лексика вернула пять подписей бланка, три из них `is_quotable`
    # отбрасывала — но уже ПОСЛЕ того, как пятёрка закрыла колчан и
    # вектор не запросился. Непригодный кандидат вытеснял пригодный
    # дважды: и из ответа, и из самой возможности поискать вектором.
    #
    # Прежний порядок объяснялся тем, что «фрагмент подняла векторная
    # ветка, значит фильтровать надо результат, а не ветку». Это
    # объяснение построено на замере 307, который тот же разбор потом
    # опроверг сам (§3 и §7.1 CHUNKING_AND_BAD_ANSWERS): вектор в том
    # вопросе не вызывался вовсе. Фильтр после обеих веток остаётся —
    # векторные находки тоже бывают шапкой, — но лексика чистится до
    # подсчёта.
    #
    # Обрезка по MAX_EVIDENCE теперь ПОСЛЕ отбраковки, а не до: иначе
    # пять строк бланка так и продолжали бы вытеснять шестого кандидата,
    # который и есть текст.
    considered_ids = {e.chunk_id for e in lexical}
    # Заметки памяти впереди документов (§14.12 "strong exact boost"),
    # но теперь В ОБЩЕМ наборе, а не вместо него — см. выше. Отбраковка
    # `is_quotable` к ним не применяется: владелец записал этот текст
    # сам, и «слишком короткий, чтобы цитировать» к его собственной
    # заметке отношения не имеет.
    memory_evidence = [
        Evidence(chunk_id=f"memory:{hit.memory_id}", source_id=hit.source_id or "",
                 chunk_text=hit.canonical_text, original_filename=None, rank=hit.rank)
        for hit in memory_hits
    ] if spec.operation != OP_VALUE else []
    quotable = memory_evidence + [e for e in lexical if is_quotable(e.chunk_text)]

    # РАВНЫЕ ПО РАНГУ РАЗЛИЧАЮТСЯ ДАТОЙ. Несколько документов совпадают
    # с вопросом одинаково («липидный профиль» есть и в самом анализе, и
    # в трёх консультациях, которые его пересказывают), и без этого
    # правила в пятёрку попадали случайные из них. Ранг остаётся
    # главным: дата решает только ничью.
    _attach_dates(session, quotable)
    quotable.sort(key=lambda item: (round(item.rank, 4), _tiebreak_freshness(item)), reverse=True)

    # ВЕКТОР СПРАШИВАЕТСЯ ВСЕГДА, а не «если лексика не набрала пять».
    #
    # Прежнее условие `if len(evidence) < MAX_EVIDENCE` экономило один
    # HTTP-вызов к embed-сервису и ровно этим делало совпадение слов
    # обязательным условием ответа — то, против чего владелец возражал
    # прямо (распоряжение 06.09.2026).
    #
    # Замер (прогон 422) показывает цену. На «в каком порядке я всё
    # делаю по прилёте» лексика вернула ровно пять медицинских PDF, все
    # с одинаковым рангом 0.01216 — то есть совпало одно общее слово, и
    # различить она не смогла ничего. Колчан был «полон», вектор не
    # спросили. А вектор на тот же вопрос давал 0.455/0.432/0.410 — три
    # собственные записи владельца про билеты, вылет и порядок действий.
    # Ответ был в одном запросе, который решили не делать.
    #
    # Fail-open остаётся: недоступный embed-сервис не отменяет
    # лексический ответ, он просто оставляет прежнее поведение.
    query_embedding = embed_texts_or_none([spec.retrieval_question])[0]
    vector_hits: list[Evidence] = []
    if query_embedding is not None:
        # Исключается всё, что лексика уже рассмотрела, а не только
        # прошедшее отбраковку: отбракованный чанк не должен вернуться
        # вторым путём.
        if search_public:
            vector_hits += _branch(_vector_search(
                session, query_embedding=query_embedding, domain=domain,
                knowledge_user_id=knowledge_user_id, exclude_chunk_ids=considered_ids,
                source_ids=focus_source_ids,
            ))
        if search_health:
            vector_hits += _branch(_health_vector_search(
                query_embedding=query_embedding, knowledge_user_id=knowledge_user_id,
                exclude_chunk_ids=considered_ids, source_ids=focus_source_ids,
            ))
        # Отбраковка по векторным находкам: они тоже бывают шапкой
        # документа. Лексика к этому месту уже чистая (см. выше).
        vector_hits = [e for e in vector_hits if is_quotable(e.chunk_text)]
        vector_hits.sort(key=lambda item: item.rank, reverse=True)

    # Полный набор — и отдельно от него пятёрка, которая уйдёт в модель.
    candidates = _merge_branches(quotable, vector_hits)

    # ТРЕБУЕМАЯ ФОРМА ОТВЕТА РЕШАЕТ, ЧТО ПОПАДЁТ В ДОКАЗАТЕЛЬСТВА.
    #
    # Прогон 443: на «по книге Линде что такое эмоционально-образная
    # терапия» поиск нашёл нужную книгу, но в пятёрку попал раздел
    # «Рекомендуемая литература» — фрагмент, где термин упомянут и не
    # определён. Порядок задавал только ранг поиска; тип ответа в
    # отборе не участвовал вовсе.
    #
    # Отбор идёт по ПОЛНОМУ набору, а не по пятёрке: определение могло
    # стоять шестым, и до модели оно бы не дошло.
    selection = select_for_operation(spec.operation, spec.question,
                                     [item.chunk_text for item in candidates])
    candidates = [candidates[index] for index in selection.order]
    form_note = None if selection.found else FORM_MISSING_NOTICE.get(spec.operation)
    evidence = candidates[:MAX_EVIDENCE]

    # СРАВНЕНИЕ БЕЗ ВТОРОЙ СТОРОНЫ СРАВНЕНИЕМ НЕ БУДЕТ.
    #
    # Распоряжение владельца 07.09.2026, п.3: «Для определения,
    # сравнения или примера проверяй, что источник действительно
    # содержит требуемый тип сведений». У определения и примера тип
    # виден по форме фрагмента, и её проверяет `select_for_operation`
    # выше. У сравнения формы нет — оно законно собирается из двух
    # документов, где ни в одном сравнения не написано. Требуемые
    # сведения для сравнения — обе стороны, и проверяется их наличие.
    #
    # Проверка идёт по ПОЛНОМУ набору кандидатов: вторая сторона могла
    # найтись шестой, и судить о ней по пятёрке значило бы отказывать
    # по недосмотру.
    if spec.operation == OP_COMPARE and candidates:
        absent = missing_comparison_side(
            spec.question, [item.chunk_text for item in candidates])
        if absent is not None:
            run_id = uuid.uuid4()
            session.add(KnowledgeAnswerRun(
                id=run_id, knowledge_user_id=knowledge_user_id,
                query_hash=query_hash(query), domain=domain,
                mode=KnowledgeAnswerMode.N0, paid_ai_used=False,
                evidence_count=len(evidence),
            ))
            return ProbeResult(
                # LOCAL_NOT_FOUND, а не LOCAL_ANSWER: сравнения нет и не
                # будет, и платный переход эта строка обязана закрыть —
                # платная модель второй стороны тоже не знает.
                outcome="LOCAL_NOT_FOUND", mode=KnowledgeAnswerMode.N0,
                answer_text=format_missing_comparison_side(
                    absent, [_source_label(e) for e in evidence]),
                answer_run_id=str(run_id), candidates=candidates,
                sources=[{"kind": "chunk", "source_id": item.source_id,
                          "chunk_id": item.chunk_id,
                          "original_filename": item.original_filename}
                         for item in evidence])

    # ── ОПЕРАЦИЯ РЕШАЕТ, ЧТО ДЕЛАТЬ С НАЙДЕННЫМ ─────────────────────
    #
    # Распоряжение владельца 07.09.2026, п.3. Счёт и перечисление
    # исполняются ДЕТЕРМИНИРОВАННО и по ПОЛНОМУ набору кандидатов, а не
    # по пятёрке, ушедшей в модель: «„Сколько" возвращает количество,
    # рассчитанное по найденному набору», «„Все" требует обхода полного
    # подходящего набора либо явного указания неполноты».
    #
    # Модель здесь не участвует вовсе: число, полученное пересчётом,
    # выдумать нельзя. Не получилось посчитать — честный отказ ниже по
    # общему пути, а не ближайшее число из текста.
    if spec.operation in (OP_COUNT, OP_ENUMERATE) and candidates:
        _attach_dates(session, candidates)
        texts = [item.chunk_text for item in candidates]
        if spec.operation == OP_COUNT:
            done = run_count(spec.question, texts)
        else:
            done = run_enumerate(
                spec.question, texts,
                complete=not any(size >= CANDIDATE_LIMIT for size in branch_sizes))
        if done is not None:
            used = [candidates[i - 1] for i in done.used]
            run_id = uuid.uuid4()
            session.add(KnowledgeAnswerRun(
                id=run_id, knowledge_user_id=knowledge_user_id,
                query_hash=query_hash(query), domain=domain,
                mode=KnowledgeAnswerMode.Z2, paid_ai_used=False,
                evidence_count=len(used),
            ))
            return ProbeResult(
                outcome="LOCAL_ANSWER", mode=KnowledgeAnswerMode.Z2,
                answer_text=format_with_sources(
                    done.text, [_source_label(e) for e in used],
                    unsupported_period=spec.time.unsupported, form_note=form_note),
                evidence=used, candidates=candidates, answer_run_id=str(run_id),
                sources=[{"kind": "chunk", "source_id": e.source_id,
                          "chunk_id": e.chunk_id,
                          "original_filename": e.original_filename}
                         for e in used])

    # ДАТЫ КАНДИДАТОВ. Одним запросом на всех, а не по одному на чанк:
    # дата нужна и чтобы ответить «в последний раз», и чтобы подписать
    # ответ числом. Health-чанки лежат в своей схеме, но строка
    # источника — общая, поэтому запрос один и тот же.
    _attach_dates(session, evidence)

    # «В последний раз», «свежий», «актуальный» — вопрос о ВРЕМЕНИ, и
    # порядок кандидатов обязан это отражать: сначала самое новое.
    # Документы без даты уходят в конец — не потому что они старые, а
    # потому что утверждать их новизну нечем.
    if spec.time.recent:
        evidence.sort(key=_freshness, reverse=True)

    # §14.13 quality gate: без evidence выше порога бесплатного ответа
    # нет. Что делать дальше, решает ОБЛАСТЬ ВОПРОСА, а не факт пустоты.
    #
    # Распоряжение владельца 06.09.2026: «Отсутствие находок не является
    # моим разрешением оплатить ответ». До этой правки пустой поиск по
    # личному вопросу уходил в платную модель, которая истории владельца
    # не знает и заполняла пустоту рассуждениями — ровно то, что
    # запрещает контракт ответа.
    #
    # Общий вопрос («переведи текст», «что такое ферритин») ведёт себя
    # как раньше: локальной памяти по нему и не должно быть что сказать,
    # запрещать по нему платную модель значило бы сломать её работу
    # заодно с запретом (§5 CHUNKING_AND_BAD_ANSWERS откладывал это
    # различение до QuerySpec; дальше откладывать нельзя).
    if not evidence:
        if not spec.personal and paid_allowed:
            return ProbeResult(outcome="NEEDS_REASONING")
        run_id = uuid.uuid4()
        session.add(KnowledgeAnswerRun(
            id=run_id,
            knowledge_user_id=knowledge_user_id,
            query_hash=query_hash(query), domain=domain,
            mode=KnowledgeAnswerMode.N0,
            paid_ai_used=False, evidence_count=0,
        ))
        return ProbeResult(outcome="LOCAL_NOT_FOUND", mode=KnowledgeAnswerMode.N0,
                           answer_text=PERSONAL_NOT_FOUND, answer_run_id=str(run_id))

    # ── P4: НЕСКОЛЬКО ФРАГМЕНТОВ СКЛАДЫВАЮТСЯ В ОТВЕТ ────────────────
    # Распоряжение владельца 06.09.2026: «Несколько найденных фрагментов
    # должны позволять собрать ответ; автоматическая выдача ближайшей
    # цитаты при количестве находок больше одной не выполняет эту
    # задачу». До этой правки ответ выбирался рангом поиска, то есть
    # совпадением слов: прогон 383 на «какое у меня было давление?»
    # отдал протокол эндоскопии, а консультация кардиолога с самим
    # давлением стояла третьей и в ответ не попадала.
    #
    # Синтез читает вопрос и ВСЕ найденные фрагменты. Три исхода
    # (synthesis.py): ответ со ссылками на фрагменты, честное «здесь
    # ответа нет» и недоступность модели. Смешивать их нельзя: первый —
    # ответ, второй — тоже ответ, третий — незнание.
    # Подпись с датой — НАША приписка, и доказательством быть не может:
    # заземление проверяется по исходному тексту чанка (см.
    # synthesis.parse_response()).
    synthesis = synthesize_or_none(spec.question,
                                   [_dated_fragment(e) for e in evidence],
                                   sources=[e.chunk_text for e in evidence])

    # ПРИМЕР, КОТОРОГО В ИСТОЧНИКАХ НЕТ.
    #
    # Распоряжение владельца 07.09.2026, п.3, третий тип сведений.
    # Определение уже проверяется на выходе (`undefined_subjects`):
    # ответ, поданный как определение, засчитывается, только если то же
    # определяемое определяет и источник. У примера такой проверки не
    # было вовсе — только примечание, которое ничему не мешало.
    #
    # Проверяется здесь, а не входным запретом синтеза. Входной запрет
    # («формы нет — модель не зовём») отбрасывал бы и законные ответы:
    # «Эмоционально-образная терапия работает с образом чувства» —
    # ответ на «что такое ЭОТ», хотя определительного оборота в нём
    # нет. Тип сведений решается по УТВЕРЖДЕНИЮ ответа, а не по тому,
    # какие слова нашлись в источнике.
    #
    # Три условия вместе: пример СПРОШЕН, в источниках примера нет
    # (`form_note`), и ответ всё-таки подан как пример. «Например» в
    # пересказе перечисления под это не попадает — там пример не
    # спрашивали.
    if (synthesis is not None and synthesis.answered
            and spec.operation == OP_EXAMPLE and form_note is not None
            and claims_example(synthesis.text)):
        # `verified=False` — это ОТКЛОНЁННЫЙ ответ, а не «данных нет»:
        # записи по вопросу есть, и владелец увидит именно это.
        synthesis = Synthesis(answered=False, verified=False)

    if synthesis is not None and not synthesis.answered:
        # НАЙДЕННОЕ ЕСТЬ — ЗНАЧИТ, ВОПРОС О ДАННЫХ ВЛАДЕЛЬЦА, и платить
        # за него нельзя ни при какой политике.
        #
        # Прежде здесь решала формулировка: `spec.mode == MODE_GENERAL`
        # открывал платный переход. Прогон 422: «что я беру с собой из
        # лекарств?» — режим general, при этом векторная ветка нашла его
        # собственную голосовую заметку про аптечку с близостью 0.633.
        # Вопрос был к его записям, запись нашлась, а право на оплату
        # выдавалось по тому, что в тексте нет слова-признака.
        #
        # Наличие находок — это и есть признак, и он не словарный:
        # корпус ответил на запрос, значит запрос его касается. Платный
        # переход остаётся только там, где локально не нашлось НИЧЕГО
        # (см. `if not evidence` выше) — там же, где его разрешает и
        # политика вызывающего.
        run_id = uuid.uuid4()
        session.add(KnowledgeAnswerRun(
            id=run_id, knowledge_user_id=knowledge_user_id,
            query_hash=query_hash(query), domain=domain, mode=KnowledgeAnswerMode.N0,
            paid_ai_used=False, evidence_count=len(evidence),
        ))
        # Источники остаются в ответе и при отказе: владельцу нужно
        # видеть, что именно было просмотрено, и иметь возможность
        # открыть это самому.
        return ProbeResult(
            # Строка исхода остаётся LOCAL_NOT_FOUND намеренно: на неё
            # завязан контракт платного перехода (находки его закрывают),
            # и новое значение здесь означало бы «исход неизвестен» для
            # плагина — то есть открытую дверь. Разводятся ТЕКСТЫ, а
            # владелец видит именно их.
            outcome="LOCAL_NOT_FOUND", mode=KnowledgeAnswerMode.N0,
            answer_text=(format_nothing_answered if synthesis.verified
                         else format_unverified)([_source_label(e) for e in evidence]),
            answer_run_id=str(run_id), candidates=candidates,
            sources=[{"kind": "chunk", "source_id": item.source_id,
                      "chunk_id": item.chunk_id,
                      "original_filename": item.original_filename}
                     for item in evidence])

    if synthesis is not None:
        # Показываются только те фрагменты, на которые модель сослалась:
        # ответ и его доказательства обязаны совпадать, иначе проверить
        # ответ нечем (§14.12, контракт ответа 05.09.2026).
        evidence = [evidence[i - 1] for i in synthesis.used]
        answer_text = format_with_sources(
            synthesis.text, [_source_label(e) for e in evidence],
            unsupported_period=spec.time.unsupported, form_note=form_note)
        mode = KnowledgeAnswerMode.Z2
    else:
        # Модель недоступна — прежний детерминированный composer. Это
        # деградация, а не отказ: одна цитата, честно названная цитатой.
        answer_text, mode = _compose_answer(evidence)

        # §14.12 Z2-рефраз (docs/KNOWLEDGE_MODELS.md, gemma2:2b выбран
        # живым замером 31.08.2026) — ТОЛЬКО для Z0 (одна цитата). Замер
        # проверял рефраз ровно одного факта за раз. paid_ai_used не
        # трогается: локальный Ollama-рефраз не платный вызов (§14.14).
        if mode == KnowledgeAnswerMode.Z0:
            rephrased = rephrase_or_none(
                session, question=query, evidence_text=evidence[0].chunk_text,
                knowledge_user_id=knowledge_user_id,
            )
            if rephrased is not None:
                cite = evidence[0].original_filename or evidence[0].source_id
                answer_text = f"{rephrased}\n\nИсточник: {cite}"

    run_id = uuid.uuid4()
    session.add(KnowledgeAnswerRun(
        id=run_id,
        knowledge_user_id=knowledge_user_id,
        query_hash=query_hash(query), domain=domain, mode=mode,
        paid_ai_used=False, evidence_count=len(evidence),
    ))
    return ProbeResult(outcome="LOCAL_ANSWER", mode=mode, answer_text=answer_text,
                       evidence=evidence, candidates=candidates, answer_run_id=str(run_id),
                       sources=[{"kind": "chunk", "source_id": item.source_id,
                                 "chunk_id": item.chunk_id,
                                 "original_filename": item.original_filename}
                                for item in evidence])
