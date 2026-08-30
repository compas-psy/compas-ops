"""v3.8 §14.12-§14.14, P8.5.12 (recall) — читающая половина «Запомни».

Фикстуры взяты дословно из acceptance-списка спеки («Recall»):
«Дай мне ссылку …», «Напомни мне номер машины курьера», «Какой был
номер вчера?», «Напомни завтра в 10 позвонить …».
"""

import uuid
from datetime import timedelta

import pytest

from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.memory import try_remember
from helm_core.knowledge.probe import probe
from helm_core.knowledge.recall import (
    is_future_reminder, is_historical_query, search_memories,
)
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (
    KnowledgeAnswerRun, KnowledgeMemory, KnowledgeMemoryStatus, KnowledgeUser, KnowledgeUserRole,
)
from helm_core.models.base import utcnow

from sqlalchemy import select

CHANNEL = "max"


@pytest.fixture
def knowledge_user(session):
    user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(user)
    session.flush()
    return user


# ── §14.13 маршрутизация намерения ───────────────────────────────────────

def test_future_reminder_phrase_is_not_memory_recall():
    assert is_future_reminder("Напомни мне завтра в 10 позвонить курьеру") is True


def test_reminder_word_without_future_trigger_is_recall():
    """§14.13 прямым текстом: «напомни [какой/номер/ссылку/что…]» без
    будущего триггера — это recall, а не постановка напоминания."""
    assert is_future_reminder("Напомни мне номер машины курьера") is False


def test_temporal_word_without_action_is_still_recall():
    """«сегодня» в вопросе о факте не делает его напоминанием — иначе
    собственный пример Micro-Memory из спеки («курьер, который приедет
    сегодня») стал бы неотвечаемым."""
    assert is_future_reminder("Напомни номер машины курьера, который приедет сегодня") is False


def test_question_without_reminder_word_is_recall():
    assert is_future_reminder("Дай мне ссылку на видео, где про наложение титров") is False


def test_historical_query_detected():
    assert is_historical_query("Какой был номер машины курьера вчера?") is True


def test_current_query_is_not_historical():
    assert is_historical_query("Напомни мне номер машины курьера") is False


# ── §14.14 точность возврата ─────────────────────────────────────────────

def test_bookmark_url_returned_byte_exact(session):
    stored = try_remember(
        session, channel=CHANNEL,
        text="Запомни: https://example.com/video?id=42 — про наложение титров")
    session.flush()

    result = probe(session, query="Дай мне ссылку на видео, где про наложение титров")

    assert result.outcome == "LOCAL_ANSWER"
    # Дословно, без единого добавленного символа: §14.14 "exact URL not
    # modified" проверяется как равенство, а не как вхождение.
    assert result.answer_text == stored.memory.canonical_text
    assert "https://example.com/video?id=42" in result.answer_text


def test_identifier_returned_exact(session):
    stored = try_remember(session, channel=CHANNEL,
                          text="Запомни: номер машины курьера — А123ВС77")
    session.flush()

    result = probe(session, query="Напомни мне номер машины курьера")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.answer_text == stored.memory.canonical_text
    assert "А123ВС77" in result.answer_text


def test_recall_makes_zero_paid_calls(session):
    """§14.14/§30: recall не вызывает ни LiteLLM, ни OpenRouter — в этом
    коде их вызывать нечем, и answer_run фиксирует это фактом."""
    try_remember(session, channel=CHANNEL, text="Запомни: код домофона 4512")
    session.flush()

    result = probe(session, query="какой код домофона")
    session.flush()

    assert result.outcome == "LOCAL_ANSWER"
    run = session.scalars(select(KnowledgeAnswerRun)).one()
    assert run.paid_ai_used is False


def test_multiple_memory_hits_are_enumerated(session):
    try_remember(session, channel=CHANNEL, text="Запомни: номер машины курьера А123ВС77")
    session.flush()
    try_remember(session, channel=CHANNEL, text="Запомни: номер машины соседа В777АА99")
    session.flush()

    result = probe(session, query="номер машины")

    assert result.mode == "Z1"
    assert len(result.memory) == 2
    assert "А123ВС77" in result.answer_text
    assert "В777АА99" in result.answer_text


# ── §14.10 истечение проверяется в момент запроса ────────────────────────

def test_expired_memory_is_excluded_from_current_recall(session):
    stored = try_remember(session, channel=CHANNEL,
                          text="Запомни: курьер приедет сегодня, машина А123ВС77")
    session.flush()
    assert stored.memory.expires_at is not None
    # Пока срок не вышел — факт находится: иначе следующая проверка
    # прошла бы вхолостую, просто ничего не найдя.
    assert probe(session, query="какая машина у курьера").outcome == "LOCAL_ANSWER"

    # Статус остаётся ACTIVE — фоновая рутина, материализующая EXPIRED,
    # не существует. Именно это и проверяем: срок обязан отсекаться
    # предикатом запроса, а не доверием к рутине (§14.10).
    stored.memory.expires_at = utcnow() - timedelta(seconds=1)
    session.flush()

    result = probe(session, query="какая машина у курьера")

    assert result.outcome == "NEEDS_REASONING"
    assert session.get(KnowledgeMemory, stored.memory.id).status == KnowledgeMemoryStatus.ACTIVE


def test_expired_memory_is_available_to_historical_query(session):
    stored = try_remember(session, channel=CHANNEL,
                          text="Запомни: номер машины курьера сегодня — А123ВС77")
    session.flush()
    stored.memory.expires_at = utcnow() - timedelta(seconds=1)
    session.flush()

    result = probe(session, query="Какой был номер машины курьера вчера?")

    assert result.outcome == "LOCAL_ANSWER"
    assert "А123ВС77" in result.answer_text


def test_expired_status_row_is_available_to_historical_query(session):
    """Тот же исход, когда статус УЖЕ материализован в EXPIRED — оба
    представления истёкшего факта ведут себя одинаково."""
    stored = try_remember(session, channel=CHANNEL,
                          text="Запомни: номер машины курьера А123ВС77")
    session.flush()
    stored.memory.status = KnowledgeMemoryStatus.EXPIRED
    session.flush()

    current = probe(session, query="Напомни мне номер машины курьера")
    historical = probe(session, query="Какой был номер машины курьера вчера?")

    assert current.outcome == "NEEDS_REASONING"
    assert historical.outcome == "LOCAL_ANSWER"


def test_disabled_memory_never_returns_even_historically(session):
    """«Забудь» (DISABLED) — не «спрятать до правильной формулировки»."""
    stored = try_remember(session, channel=CHANNEL,
                          text="Запомни: номер машины курьера А123ВС77")
    session.flush()
    assert probe(session, query="Напомни мне номер машины курьера").outcome == "LOCAL_ANSWER"

    stored.memory.status = KnowledgeMemoryStatus.DISABLED
    session.flush()

    assert probe(session, query="Напомни мне номер машины курьера").outcome == "NEEDS_REASONING"
    assert probe(session, query="Какой был номер машины вчера?").outcome == "NEEDS_REASONING"


# ── §14.13 напоминание не съедает вопрос к памяти и наоборот ─────────────

def test_future_reminder_does_not_answer_from_memory(session):
    """Даже когда в памяти есть подходящий факт: «напомни завтра в 10
    позвонить курьеру» — это не запрос номера курьера."""
    try_remember(session, channel=CHANNEL, text="Запомни: телефон курьера +79990000000")
    session.flush()
    # Тот же факт по обычному вопросу находится — значит NEEDS_REASONING
    # ниже вызван маршрутизацией намерения, а не пустой памятью.
    assert probe(session, query="Напомни телефон курьера").outcome == "LOCAL_ANSWER"
    session.flush()
    runs_before = len(session.scalars(select(KnowledgeAnswerRun)).all())

    result = probe(session, query="Напомни мне завтра в 10 позвонить курьеру")
    session.flush()

    assert result.outcome == "NEEDS_REASONING"
    assert result.answer_text is None
    # И не пишет answer_run: локального ответа не было.
    assert len(session.scalars(select(KnowledgeAnswerRun)).all()) == runs_before


# ── §14.12 приоритет памяти над документами ──────────────────────────────

def test_memory_takes_priority_over_document_chunks(session):
    ingest_text(session, domain="personal", text="Номер машины курьера был В000АА00.",
                original_filename="old-notes.md")
    session.flush()
    try_remember(session, channel=CHANNEL,
                 text="Запомни: номер машины курьера — А123ВС77")
    session.flush()

    result = probe(session, query="Напомни мне номер машины курьера")

    assert result.memory and not result.evidence
    assert result.answer_text == "номер машины курьера — А123ВС77"


def test_document_answer_still_works_when_memory_has_no_match(session):
    """Регрессия: документный путь не сломан появлением памяти."""
    ingest_text(session, domain="engineering", text="Встречу перенесли на четверг.",
                original_filename="meeting-notes.md")
    session.flush()

    result = probe(session, query="когда встреча")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.evidence and not result.memory
    assert "meeting-notes.md" in result.answer_text


# ── v3.8 §14.4 tenant-изоляция recall ────────────────────────────────────

def test_memory_of_one_user_is_never_recalled_by_another(session, knowledge_user):
    owner_id = bind_knowledge_user(session, None)
    try_remember(session, channel=CHANNEL, text="Запомни: код сейфа владельца 1234",
                 knowledge_user_id=owner_id)
    session.flush()

    other = probe(session, query="какой код сейфа", knowledge_user_id=knowledge_user.id)

    assert other.outcome == "NEEDS_REASONING"
    # А у владельца тот же вопрос отвечается — значит дело в тенанте, а
    # не в том, что запрос вообще ничего не находит.
    assert probe(session, query="какой код сейфа",
                 knowledge_user_id=owner_id).outcome == "LOCAL_ANSWER"


def test_search_memories_filters_by_tenant_directly(session, knowledge_user):
    owner_id = bind_knowledge_user(session, None)
    try_remember(session, channel=CHANNEL, text="Запомни: пропуск в офис 8891",
                 knowledge_user_id=owner_id)
    session.flush()

    bind_knowledge_user(session, knowledge_user.id)
    hits = search_memories(session, query="пропуск в офис",
                           knowledge_user_id=knowledge_user.id, now=utcnow())

    assert hits == []


def test_search_memories_returns_nothing_for_unknown_tenant(session):
    try_remember(session, channel=CHANNEL, text="Запомни: пропуск в офис 8891")
    session.flush()

    bind_knowledge_user(session, None)
    hits = search_memories(session, query="пропуск в офис",
                           knowledge_user_id=uuid.uuid4(), now=utcnow())

    assert hits == []
