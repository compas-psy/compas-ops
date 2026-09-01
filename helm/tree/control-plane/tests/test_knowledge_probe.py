"""HELM Knowledge (ТЗ §14, §30.8.5) — лексический слой, P8.5.1/8.5.5.

Golden cases по §30.8.5 в достижимом сейчас объёме (без embeddings,
GigaAM, Graphify — см. V3.4-DELTA.md): exact fact, RU lexical mismatch,
absent-from-corpus, health ACL isolation, SHA256-дедуп.
"""

from sqlalchemy import select

from helm_core.knowledge import ingest as ingest_module
from helm_core.knowledge import probe as probe_module
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.probe import MIN_RANK_SCORE, probe
from helm_core.models import (
    KnowledgeAnswerRun, KnowledgeChunk, KnowledgeSource, KnowledgeUser, KnowledgeUserRole,
)
from helm_core.models.tables import KNOWLEDGE_EMBED_DIM

from conftest import SYSTEM_OWNER_ID


# ── §14.5: дедуп по SHA256 ────────────────────────────────────────────────

def test_ingest_same_text_does_not_duplicate(session):
    first = ingest_text(session, domain="engineering", text="Решение: используем Postgres.")
    session.flush()
    second = ingest_text(session, domain="engineering", text="Решение: используем Postgres.")
    session.flush()

    assert first.id == second.id
    assert len(session.scalars(select(KnowledgeSource)).all()) == 1


def test_ingest_splits_paragraphs_into_chunks(session):
    text = "Первый абзац про кота.\n\nВторой абзац про собаку."
    source = ingest_text(session, domain="personal", text=text)
    session.flush()

    chunks = session.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)
        .order_by(KnowledgeChunk.ordinal)
    ).all()
    assert [c.text for c in chunks] == ["Первый абзац про кота.", "Второй абзац про собаку."]


# ── §30.8.5 Retrieval golden cases ────────────────────────────────────────

def test_exact_fact_returns_local_answer(session):
    """«exact fact»: единственное совпадение → Z0, extractive, с источником."""
    ingest_text(session, domain="engineering", text="Встречу перенесли на четверг.",
               original_filename="meeting-notes.md")
    session.flush()

    result = probe(session, query="когда встреча")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.mode == "Z0"
    assert "четверг" in result.answer_text
    assert "meeting-notes.md" in result.answer_text


def test_russian_lexical_wording_mismatch_still_matches(session):
    """«Russian lexical wording mismatch»: PostgreSQL FTS для RU стеммирует
    словоформы — «решениях» находится по запросу «решение» без точного
    совпадения строки."""
    ingest_text(session, domain="engineering", text="Мы приняли важное решение о миграции.")
    session.flush()

    result = probe(session, query="какие решения приняли")

    assert result.outcome == "LOCAL_ANSWER"


def test_question_absent_from_corpus_escalates(session):
    """«question absent from corpus» → NEEDS_REASONING, не выдуманный ответ."""
    ingest_text(session, domain="engineering", text="Решение: используем Postgres.")
    session.flush()

    result = probe(session, query="какая погода в Токио")

    assert result.outcome == "NEEDS_REASONING"
    assert result.answer_text is None


def test_empty_corpus_escalates(session):
    result = probe(session, query="что угодно")
    assert result.outcome == "NEEDS_REASONING"


def test_multiple_matches_produce_z1_structured_list(session):
    ingest_text(session, domain="engineering", text="Решение №1: используем Postgres.")
    ingest_text(session, domain="engineering", text="Решение №2: используем Docker.")
    ingest_text(session, domain="engineering", text="Решение №3: используем Caddy.")
    session.flush()

    result = probe(session, query="какие решения приняли по инфраструктуре")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.mode == "Z1"
    assert "Найдено" in result.answer_text


# ── health: решение владельца 01.09.2026 — не исключение из общего поиска ──

def test_health_domain_reachable_from_general_query(session):
    """Решение владельца 01.09.2026: «все домены должны относиться к
    бесплатному второму мозгу» — health отвечает наравне со всеми
    остальными доменами и без явного domain=health. Предыдущая версия
    этого теста требовала обратного (§14.15 «chief не получает raw
    health RAG на общий вопрос») — решение владельца отменяет это
    прочтение спеки прямо, не тихо."""
    ingest_text(session, domain="health", text="Анализ крови показал дефицит железа.")
    session.flush()

    result = probe(session, query="что там с анализом крови")

    assert result.outcome == "LOCAL_ANSWER"


def test_health_domain_reachable_with_explicit_scope(session):
    """Явный health-scope (domain='health') по-прежнему работает — теперь
    просто не единственный путь к health-контенту."""
    ingest_text(session, domain="health", text="Анализ крови показал дефицит железа.")
    session.flush()

    result = probe(session, query="что там с анализом крови", domain="health")

    assert result.outcome == "LOCAL_ANSWER"


def test_zapiski_domain_excluded_from_general_query(session):
    """§14.15: 'ЗАПИСКИ client content: NEVER AUTO-INGEST ... not indexed
    into general namespaces' — защита приватности КЛИЕНТА, единственное
    оставшееся исключение из общего поиска (health им больше не является,
    решение владельца 01.09.2026)."""
    ingest_text(session, domain="simpas/zapiski", text="Клиент рассказал про тревогу на работе.")
    session.flush()

    result = probe(session, query="что там про тревогу на работе")

    assert result.outcome == "NEEDS_REASONING", (
        "simpas/zapiski не должен попадать в обычный поиск без явного domain (§14.15)"
    )


def test_zapiski_domain_reachable_with_explicit_scope(session):
    ingest_text(session, domain="simpas/zapiski", text="Клиент рассказал про тревогу на работе.")
    session.flush()

    result = probe(session, query="что там про тревогу на работе", domain="simpas/zapiski")

    assert result.outcome == "LOCAL_ANSWER"


def test_general_query_does_not_leak_across_other_domains_by_mistake(session):
    """Явный domain-фильтр не даёт постороннему контенту просочиться —
    базовая проверка, что фильтр domain реально применяется, а не игнорируется."""
    ingest_text(session, domain="ventures", text="Инвестор согласился на раунд A.")
    session.flush()

    result = probe(session, query="что с раундом", domain="personal")

    assert result.outcome == "NEEDS_REASONING"


# ── §14.14: paid-AI avoidance metrics ─────────────────────────────────────

def test_local_answer_logs_answer_run_without_paid_ai(session):
    ingest_text(session, domain="engineering", text="Решение: используем Postgres.")
    session.flush()

    probe(session, query="какое решение приняли")
    session.flush()

    run = session.scalars(select(KnowledgeAnswerRun)).one()
    assert run.paid_ai_used is False
    assert run.mode == "Z0"
    assert run.evidence_count == 1


def test_needs_reasoning_does_not_log_answer_run():
    """NEEDS_REASONING логируется вызывающим кодом ПОСЛЕ реального ответа
    Hermes (cloud_model/latency известны только тогда) — probe() сам по
    себе строку не пишет. Проверяется на уровне probe.py::probe напрямую,
    без БД: если бы строка писалась здесь, для этого потребовалась бы сессия.
    """
    import inspect

    from helm_core.knowledge import probe as probe_module

    source = inspect.getsource(probe_module.probe)
    assert "NEEDS_REASONING" in source
    # Единственное место, где создаётся KnowledgeAnswerRun — ветка после
    # успешной композиции ответа, не до неё.
    needs_reasoning_line = source.index('outcome="NEEDS_REASONING")')
    answer_run_line = source.index("KnowledgeAnswerRun(")
    assert needs_reasoning_line < answer_run_line


# ── §14.13 quality gate ────────────────────────────────────────────────────

def test_local_answer_evidence_never_below_threshold(session):
    """§14.13: то, что дошло до LOCAL_ANSWER, обязано пройти порог —
    проверка механизма фильтрации на реальном (не сконструированном под
    конкретное число) корпусе, а не догадка о точном значении ts_rank."""
    ingest_text(session, domain="engineering", text="Решение: используем Postgres.")
    session.flush()

    result = probe(session, query="какое решение приняли")

    assert result.outcome == "LOCAL_ANSWER"
    assert all(e.rank >= MIN_RANK_SCORE for e in result.evidence)


# ── ADR-025 Phase 2: pgvector дополняет лексику ────────────────────────────
#
# Реальная модель — не детерминированная функция текста, замер её
# качества сделан отдельно (embed_benchmark.py, живой сервер, см.
# ADR-025). Здесь эмбеддинг подменяется управляемым one-hot вектором —
# тесты ниже проверяют проводку (hybrid orchestration в probe.py:
# tenant/domain-фильтр, исключение уже найденных лексикой чанков,
# fail-open), не качество самой модели.

def _one_hot_embedding(index: int) -> list[float]:
    vector = [0.0] * KNOWLEDGE_EMBED_DIM
    vector[index] = 1.0
    return vector


def test_semantic_paraphrase_without_shared_stems_now_matches(session, monkeypatch):
    """До Phase 2 такой запрос эскалировался бы (см.
    test_question_absent_from_corpus_escalates выше: тот же класс —
    «есть только перефразировка, ни одного общего словного корня») —
    теперь его находит _vector_search."""
    same_topic = _one_hot_embedding(0)
    monkeypatch.setattr(ingest_module, "embed_texts_or_none",
                        lambda texts: [same_topic for _ in texts])
    monkeypatch.setattr(probe_module, "embed_texts_or_none",
                        lambda texts: [same_topic for _ in texts])

    ingest_text(session, domain="engineering", text="Мигрируем базу на Postgres.",
               original_filename="infra-note.md")
    session.flush()

    result = probe(session, query="что там с хранилищем данных")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.mode == "Z0"
    assert "infra-note.md" in result.answer_text


def test_vector_search_skipped_when_embed_service_unavailable(session, monkeypatch):
    """Fail-open (ADR-025): недоступный embed-сервис не роняет и не
    блокирует probe() — деградация до чисто лексического поведения, как
    было до Phase 2, а не исключение."""
    monkeypatch.setattr(probe_module, "embed_texts_or_none",
                        lambda texts: [None] * len(texts))

    ingest_text(session, domain="engineering", text="Решение: используем Postgres.")
    session.flush()

    result = probe(session, query="какая погода в Токио")

    assert result.outcome == "NEEDS_REASONING"
    assert result.answer_text is None


def test_vector_search_does_not_leak_across_tenants(session, monkeypatch):
    """§30.8.5 «cross-user pgvector result 0»: `_vector_search` обязана
    держать тот же tenant-предикат, что `_lexical_search` (см.
    test_knowledge_tenancy.py) — проверено отдельно, а не по аналогии,
    потому что это отдельный запрос с собственным WHERE."""
    other_user = KnowledgeUser(role=KnowledgeUserRole.KNOWLEDGE_USER)
    session.add(other_user)
    session.flush()

    same_vector = _one_hot_embedding(1)
    monkeypatch.setattr(ingest_module, "embed_texts_or_none",
                        lambda texts: [same_vector for _ in texts])
    monkeypatch.setattr(probe_module, "embed_texts_or_none",
                        lambda texts: [same_vector for _ in texts])

    ingest_text(session, domain="engineering", text="Заметка чужого пользователя про облако.",
               knowledge_user_id=other_user.id)
    session.flush()

    result = probe(session, query="что там с инфраструктурой у нас")

    assert result.outcome == "NEEDS_REASONING"


# ── §14.12 Z2-рефраз (gemma2:2b, живой замер 31.08.2026) ───────────────────
#
# rephrase.py — своя HTTP-логика, замокана здесь тестами
# test_knowledge_rephrase.py; здесь проверяется только ПРОВОДКА в
# probe.py — когда рефраз применяется (Z0), когда нет (Z1, недоступность
# Ollama) — реальная сеть не нужна ни одному тесту.

def test_z0_answer_uses_rephrase_when_available(session, monkeypatch):
    monkeypatch.setattr(probe_module, "rephrase_or_none",
                        lambda session, **kw: "Живой пересказ факта.")

    ingest_text(session, domain="engineering", text="Встречу перенесли на четверг.",
               original_filename="meeting-notes.md")
    session.flush()

    result = probe(session, query="когда встреча")

    assert result.mode == "Z0"
    assert result.answer_text == "Живой пересказ факта.\n\nИсточник: meeting-notes.md"


def test_z0_answer_falls_back_to_raw_text_when_rephrase_unavailable(session, monkeypatch):
    """Fail-open явно (не полагаясь на реальный сетевой сбой, как
    остальные Z0-тесты этого файла) — тот же корректный деградированный
    путь, что "модель не прошла бенчмарк" (KNOWLEDGE_MODELS.md)."""
    monkeypatch.setattr(probe_module, "rephrase_or_none", lambda session, **kw: None)

    ingest_text(session, domain="engineering", text="Встречу перенесли на четверг.",
               original_filename="meeting-notes.md")
    session.flush()

    result = probe(session, query="когда встреча")

    assert result.mode == "Z0"
    assert result.answer_text == "Встречу перенесли на четверг.\n\nИсточник: meeting-notes.md"


def test_z1_answer_is_never_rephrased(session, monkeypatch):
    """Замер (docs/KNOWLEDGE_MODELS.md) проверял рефраз ровно ОДНОГО
    факта — совмещать несколько разных находок в одном вызове модели
    непроверено, сознательно нетронутая часть, не забытая."""
    monkeypatch.setattr(probe_module, "rephrase_or_none",
                        lambda session, **kw: "НЕ ДОЛЖНО ПОЯВИТЬСЯ")

    ingest_text(session, domain="engineering", text="Решение №1: используем Postgres.")
    ingest_text(session, domain="engineering", text="Решение №2: используем Docker.")
    ingest_text(session, domain="engineering", text="Решение №3: используем Caddy.")
    session.flush()

    result = probe(session, query="какие решения приняли по инфраструктуре")

    assert result.mode == "Z1"
    assert "НЕ ДОЛЖНО ПОЯВИТЬСЯ" not in result.answer_text
    assert "Найдено 3 совпадений" in result.answer_text


def test_z0_rephrase_receives_question_and_evidence_text(session, monkeypatch):
    captured = {}

    def fake_rephrase_or_none(session, **kw):
        captured.update(kw)
        return None

    monkeypatch.setattr(probe_module, "rephrase_or_none", fake_rephrase_or_none)

    ingest_text(session, domain="engineering", text="Встречу перенесли на четверг.",
               original_filename="meeting-notes.md")
    session.flush()

    probe(session, query="когда встреча")

    assert captured["question"] == "когда встреча"
    assert captured["evidence_text"] == "Встречу перенесли на четверг."
    assert captured["knowledge_user_id"] == SYSTEM_OWNER_ID
