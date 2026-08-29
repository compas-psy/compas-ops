"""HELM Knowledge (ТЗ §14, §30.8.5) — лексический слой, P8.5.1/8.5.5.

Golden cases по §30.8.5 в достижимом сейчас объёме (без embeddings,
GigaAM, Graphify — см. V3.4-DELTA.md): exact fact, RU lexical mismatch,
absent-from-corpus, health ACL isolation, SHA256-дедуп.
"""

from sqlalchemy import select

from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.probe import MIN_RANK_SCORE, probe
from helm_core.models import KnowledgeAnswerRun, KnowledgeChunk, KnowledgeSource


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


# ── §14.15 ACL: health не входит в общий поиск по умолчанию ──────────────

def test_health_domain_excluded_from_general_query(session):
    ingest_text(session, domain="health", text="Анализ крови показал дефицит железа.")
    session.flush()

    result = probe(session, query="что там с анализом крови")

    assert result.outcome == "NEEDS_REASONING", (
        "health не должен попадать в обычный поиск без явного domain (§14.15)"
    )


def test_health_domain_reachable_with_explicit_scope(session):
    """Явный health-scope (domain='health') — доступ есть, это другой путь,
    не общий поиск, требующий отдельного reviewer-разрешения по спеке."""
    ingest_text(session, domain="health", text="Анализ крови показал дефицит железа.")
    session.flush()

    result = probe(session, query="что там с анализом крови", domain="health")

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
