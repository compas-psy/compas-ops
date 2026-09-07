"""HELM Knowledge (ТЗ §14, §30.8.5) — лексический слой, P8.5.1/8.5.5.

Golden cases по §30.8.5 в достижимом сейчас объёме (без embeddings,
GigaAM, Graphify — см. V3.4-DELTA.md): exact fact, RU lexical mismatch,
absent-from-corpus, health ACL isolation, SHA256-дедуп.
"""

import uuid

from sqlalchemy import select

from helm_core.knowledge import chunking as chunking_module
from helm_core.knowledge import probe as probe_module
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.probe import probe
from helm_core.knowledge.query_spec import DialogueContext
from helm_core.knowledge.synthesis import Synthesis
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
    """Абзацы длиннее минимума остаются отдельными чанками. Короткие с
    06.09.2026 склеиваются (chunking.py, MIN_CHUNK_CHARS): чанк из трёх
    слов нельзя ни процитировать, ни осмысленно проранжировать — это и
    была причина перечанковки."""
    first = "Первый абзац про кота. " * 12
    second = "Второй абзац про собаку. " * 12
    source = ingest_text(session, domain="personal", text=f"{first}\n\n{second}")
    session.flush()

    chunks = session.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)
        .order_by(KnowledgeChunk.ordinal)
    ).all()
    assert len(chunks) == 2
    assert "кота" in chunks[0].text and "собаку" not in chunks[0].text
    assert "собаку" in chunks[1].text


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


def test_multiple_matches_return_one_nearest_not_a_list(session):
    """Перечисление найденного заменено одним ближайшим фрагментом
    (контракт владельца 05.09.2026). Режим прежний: уровень ответа тот
    же, изменился способ его сказать."""
    ingest_text(session, domain="engineering", text="Решение №1: используем Postgres.")
    ingest_text(session, domain="engineering", text="Решение №2: используем Docker.")
    ingest_text(session, domain="engineering", text="Решение №3: используем Caddy.")
    session.flush()

    result = probe(session, query="какие решения приняли по инфраструктуре")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.mode == "Z1"
    assert "Найдено" not in result.answer_text
    assert result.answer_text.startswith("Не нашёл прямого ответа.")
    # Ровно один фрагмент, а не три: у трёх решений общий корень «решен»,
    # и раньше все три уходили владельцу пронумерованным списком.
    assert sum(result.answer_text.count(f"Решение №{i}") for i in (1, 2, 3)) == 1


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

    result = probe(session, query="что было в анализе крови")

    assert result.outcome == "LOCAL_ANSWER"


def test_health_domain_reachable_with_explicit_scope(session):
    """Явный health-scope (domain='health') по-прежнему работает — теперь
    просто не единственный путь к health-контенту."""
    ingest_text(session, domain="health", text="Анализ крови показал дефицит железа.")
    session.flush()

    result = probe(session, query="что было в анализе крови", domain="health")

    assert result.outcome == "LOCAL_ANSWER"


def test_zapiski_domain_excluded_from_general_query(session):
    """§14.15: 'ЗАПИСКИ client content: NEVER AUTO-INGEST ... not indexed
    into general namespaces' — защита приватности КЛИЕНТА, единственное
    оставшееся исключение из общего поиска (health им больше не является,
    решение владельца 01.09.2026)."""
    ingest_text(session, domain="simpas/zapiski", text="Клиент рассказал про тревогу на работе.")
    session.flush()

    result = probe(session, query="про тревогу на работе")

    assert result.outcome == "NEEDS_REASONING", (
        "simpas/zapiski не должен попадать в обычный поиск без явного domain (§14.15)"
    )


def test_zapiski_domain_reachable_with_explicit_scope(session):
    ingest_text(session, domain="simpas/zapiski", text="Клиент рассказал про тревогу на работе.")
    session.flush()

    result = probe(session, query="про тревогу на работе", domain="simpas/zapiski")

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
    # Четыре слова — минимум `is_quotable()`: фрагмент из трёх слов не
    # годится в цитату-ответ и до строки прогона не доходит вовсе.
    ingest_text(session, domain="engineering", text="Решение по базе: используем Postgres.")
    session.flush()

    probe(session, query="какое решение приняли")
    session.flush()

    run = session.scalars(select(KnowledgeAnswerRun)).one()
    assert run.paid_ai_used is False
    assert run.mode == "Z0"
    assert run.evidence_count == 1


def test_needs_reasoning_does_not_log_answer_run(monkeypatch):
    """NEEDS_REASONING логируется вызывающим кодом ПОСЛЕ реального ответа
    Hermes (cloud_model/latency известны только тогда) — probe() сам по
    себе строку не пишет.

    Проверка поведенческая. Прежняя сравнивала СМЕЩЕНИЯ в исходнике
    («NEEDS_REASONING встречается раньше, чем KnowledgeAnswerRun») и
    сломалась 06.09.2026 от появления ветки уточнения выше по коду —
    хотя проверяемое свойство осталось верным. Позиция в тексте функции
    не свойство, а совпадение.
    """
    from helm_core.knowledge import probe as probe_module

    class _RecordingSession:
        def __init__(self):
            self.added = []

        def add(self, row):
            self.added.append(row)

    monkeypatch.setattr(probe_module, "bind_knowledge_user", lambda s, u: uuid.uuid4())
    monkeypatch.setattr(probe_module, "is_future_reminder", lambda q: False)
    monkeypatch.setattr(probe_module, "search_memories", lambda *a, **kw: [])
    monkeypatch.setattr(probe_module, "detect_intent", lambda q: "unsupported")
    monkeypatch.setattr(probe_module, "_lexical_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_module, "_health_lexical_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_module, "embed_texts_or_none", lambda texts: [None])

    fake = _RecordingSession()
    # Вопрос общий: личный дал бы LOCAL_NOT_FOUND, и это уже другая ветка.
    result = probe_module.probe(fake, query="переведи этот текст на английский")

    assert result.outcome == "NEEDS_REASONING"
    assert fake.added == [], "probe записал строку прогона там, где не должен"


# ── §14.13 quality gate ────────────────────────────────────────────────────

def test_a_longer_question_about_the_same_thing_is_not_punished(session):
    """Замер 06.09.2026: тот же документ, то же совпадение, ранг 0.02026
    на коротком вопросе и 0.01520 на длинном — `ts_rank` считает долю
    совпавших лексем от всего запроса. Абсолютный порог на этой величине
    наказывал за подробность вопроса; порога больше нет, и оба вопроса
    обязаны дойти до ответа."""
    ingest_text(session, domain="engineering", text="Решение №1: используем Postgres.")
    ingest_text(session, domain="engineering", text="Решение №2: используем Docker.")
    session.flush()

    short = probe(session, query="какое решение приняли")
    long = probe(session, query="какие решения приняли по инфраструктуре")

    assert short.outcome == "LOCAL_ANSWER"
    assert long.outcome == "LOCAL_ANSWER", "длинный вопрос о том же остался без ответа"


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
    monkeypatch.setattr(chunking_module, "embed_texts_or_none",
                        lambda texts: [same_topic for _ in texts])
    monkeypatch.setattr(probe_module, "embed_texts_or_none",
                        lambda texts: [same_topic for _ in texts])

    ingest_text(session, domain="engineering", text="Мигрируем базу на Postgres.",
               original_filename="infra-note.md")
    session.flush()

    result = probe(session, query="как у нас с хранилищем данных")

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
    monkeypatch.setattr(chunking_module, "embed_texts_or_none",
                        lambda texts: [same_vector for _ in texts])
    monkeypatch.setattr(probe_module, "embed_texts_or_none",
                        lambda texts: [same_vector for _ in texts])

    ingest_text(session, domain="engineering", text="Заметка чужого пользователя про облако.",
               knowledge_user_id=other_user.id)
    session.flush()

    result = probe(session, query="как у нас с инфраструктурой")

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
    assert result.answer_text.startswith("Не нашёл прямого ответа.")


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


# ── пороги чанков и памяти разведены ─────────────────────────────────
#
# Перечанковка 06.09.2026 подняла медиану чанка с 65 символов до 288, а
# `ts_rank(normalization=2)` делит ранг на длину. Ранги уехали под порог
# и лексика замолчала: 0–1 попадание на восьми вопросах, включая четыре,
# ответ на которые в корпусе есть (прогон 373). Базы для этих проверок
# не нужно — они про выбор чисел, а не про данные.

def test_chunk_ranking_does_not_divide_by_length():
    """Деление на длину привязывает порог к нарезке. Нарезка меняется —
    порог молча перестаёт работать, и это уже произошло один раз."""
    from helm_core.knowledge.probe import CHUNK_RANK_NORMALIZATION

    assert CHUNK_RANK_NORMALIZATION == 0


def test_chunks_have_no_absolute_rank_threshold_any_more():
    """Порог 0.02 был выбран как середина зазора «шум 0.01216 против
    ответа 0.03040» на вопросах разведки 378 — и на вопросах другой
    длины этот зазор не существует (замер 06.09.2026, см. комментарий в
    probe.py). Числа, зависящего от длины вопроса, в гейте быть не
    должно; отвечает ли найденное на вопрос, решает синтез."""
    import inspect

    from helm_core.knowledge import probe as probe_mod

    assert not hasattr(probe_mod, "MIN_CHUNK_RANK_SCORE")
    assert "MIN_CHUNK_RANK_SCORE" not in inspect.getsource(probe_mod.probe)


def test_memory_keeps_its_own_threshold():
    """Память — короткие фразы, у них деление на длину осмысленно, и
    0.003 под них откалиброван. Одно число на два разных текста уже
    однажды связало их судьбы; разводить обратно нельзя."""
    import inspect

    from helm_core.knowledge import probe as probe_mod

    source = inspect.getsource(probe_mod.probe)
    assert "hit.rank >= MIN_RANK_SCORE" in source, "порог памяти уехал вместе с чанками"


# ── P4: ответ собирается из НЕСКОЛЬКИХ фрагментов ─────────────────────────
#
# Распоряжение владельца 06.09.2026: «Несколько найденных фрагментов
# должны позволять собрать ответ; автоматическая выдача ближайшей цитаты
# при количестве находок больше одной не выполняет эту задачу».
#
# Сама модель здесь подменяется: её качество — вопрос живого замера, а не
# unit-теста (тот же принцип, что у эмбеддингов выше). Проверяется
# проводка: что в синтез уходят ВСЕ найденные фрагменты, что наружу
# уходят ровно процитированные источники и что три исхода синтеза
# (ответ / «здесь ответа нет» / модель недоступна) ведут себя по-разному.

def _health_and_vector_off(monkeypatch):
    monkeypatch.setattr(probe_module, "_health_lexical_search", lambda *a, **kw: [])
    monkeypatch.setattr(probe_module, "embed_texts_or_none", lambda texts: [None])


def test_answer_follows_the_content_of_the_fragments_not_their_rank(session, monkeypatch):
    """Живой прогон 383: на «какое у меня было давление?» первым по рангу
    встал протокол эндоскопии, а консультация с самим давлением была
    третьей и в ответ не попадала. Ответ обязан идти за содержанием."""
    _health_and_vector_off(monkeypatch)
    ingest_text(session, domain="health",
                text="Эзофагогастродуоденоскопия. Давление на стенку пищевода в норме. "
                     "Осмотр выполнен под местной анестезией, жалоб нет.",
                original_filename="эндоскопия.pdf")
    ingest_text(session, domain="health",
                text="Консультация кардиолога. Давление 120/80 мм рт. ст., пульс 68.",
                original_filename="кардиолог.pdf")
    session.flush()

    seen = {}

    def fake_synthesis(question, fragments, **_):
        seen["fragments"] = fragments
        picked = next(i for i, f in enumerate(fragments, start=1) if "120/80" in f)
        return Synthesis(answered=True, text="Давление 120/80 мм рт. ст.", used=(picked,))

    monkeypatch.setattr(probe_module, "synthesize_or_none", fake_synthesis)
    result = probe(session, query="какое у меня было давление")

    assert len(seen["fragments"]) >= 2, "в синтез ушёл не весь найденный материал"
    assert result.outcome == "LOCAL_ANSWER"
    assert result.mode == "Z2"
    assert "120/80" in result.answer_text
    assert [s["original_filename"] for s in result.sources] == ["кардиолог.pdf"], \
        "наружу ушли не процитированные источники"


def test_the_answer_names_its_sources(session, monkeypatch):
    """Владелец обязан видеть, по чему собран ответ, — иначе проверить
    его нечем (контракт ответа 05.09.2026, п. 4 аудита 06.09.2026)."""
    _health_and_vector_off(monkeypatch)
    ingest_text(session, domain="health", text="Консультация кардиолога. Давление 120/80.",
                original_filename="кардиолог.pdf")
    session.flush()
    monkeypatch.setattr(probe_module, "synthesize_or_none",
                        lambda q, f, **_: Synthesis(answered=True, text="Давление 120/80.", used=(1,)))

    result = probe(session, query="какое у меня было давление")

    assert "кардиолог.pdf" in result.answer_text


def test_model_says_the_fragments_do_not_answer_and_that_is_free(session, monkeypatch):
    """«Отсутствие находок не является моим разрешением оплатить ответ»:
    прочитанное и не подошедшее — тоже отсутствие находок."""
    _health_and_vector_off(monkeypatch)
    ingest_text(session, domain="health", text="Консультация кардиолога. Давление 120/80.",
                original_filename="кардиолог.pdf")
    session.flush()
    monkeypatch.setattr(probe_module, "synthesize_or_none",
                        lambda q, f, **_: Synthesis(answered=False))

    result = probe(session, query="какое у меня было давление")

    assert result.outcome == "LOCAL_NOT_FOUND"
    assert result.mode == "N0"
    assert "кардиолог.pdf" in result.answer_text, "не видно, что именно было просмотрено"
    assert result.sources, "источники обязаны остаться и при отказе"
    run = session.scalars(select(KnowledgeAnswerRun)).all()[-1]
    assert run.paid_ai_used is False


def test_general_question_still_escalates_when_the_fragments_do_not_answer(session, monkeypatch):
    """Правила остальных направлений не меняются: общий вопрос, на
    который найденное не отвечает, идёт к платной модели, как и раньше."""
    _health_and_vector_off(monkeypatch)
    ingest_text(session, domain="engineering",
                text="Английский язык на проекте используется в коммитах и в документации.",
                original_filename="conventions.md")
    session.flush()
    monkeypatch.setattr(probe_module, "synthesize_or_none",
                        lambda q, f, **_: Synthesis(answered=False))

    result = probe(session, query="переведи этот текст на английский")

    assert result.outcome == "NEEDS_REASONING"


def test_unavailable_model_degrades_to_a_quote_not_to_silence(session, monkeypatch):
    """Fail-open: недоступность локальной модели не отменяет ответ —
    уходит прежняя детерминированная цитата."""
    _health_and_vector_off(monkeypatch)
    monkeypatch.setattr(probe_module, "synthesize_or_none", lambda q, f, **_: None)
    monkeypatch.setattr(probe_module, "rephrase_or_none", lambda *a, **kw: None)
    ingest_text(session, domain="engineering", text="Встречу перенесли на четверг.",
                original_filename="meeting-notes.md")
    session.flush()

    result = probe(session, query="когда встреча")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.mode == "Z0"
    assert "четверг" in result.answer_text


def test_dialogue_context_confines_the_search_to_the_named_document(session, monkeypatch):
    """«Что там прописал врач?» — «там» это документ прошлого ответа.
    Искать по всему корпусу значило бы снова угадывать, о чём речь."""
    _health_and_vector_off(monkeypatch)
    first = ingest_text(session, domain="health",
                        text="Приём терапевта. Назначен приём препарата А три раза в день.",
                        original_filename="терапевт.pdf")
    ingest_text(session, domain="health",
                text="Приём хирурга. Назначен препарат Б однократно.",
                original_filename="хирург.pdf")
    session.flush()

    seen = {}

    def fake_synthesis(question, fragments, **_):
        seen["fragments"] = fragments
        return Synthesis(answered=True, text="Препарат А три раза в день.", used=(1,))

    monkeypatch.setattr(probe_module, "synthesize_or_none", fake_synthesis)
    result = probe(session, query="что там прописал врач?",
                   context=DialogueContext(question="что было на приёме терапевта?",
                                           source_ids=(str(first.id),), memory=True))

    assert seen["fragments"], "поиск не дошёл до синтеза"
    assert all("хирург" not in f for f in seen["fragments"]), \
        "поиск вышел за пределы названного документа"
    assert [s["original_filename"] for s in result.sources] == ["терапевт.pdf"]


# ── «в последний раз»: ответ обязан знать даты своих документов ──────────
#
# Живой ответ владельцу 07.09.2026: он сдавал анализ дважды за неделю,
# спросил последний — получил первый. Про время вопроса система не знала
# ничего: даты документов нигде не хранились, а весь корпус загружен
# одной пачкой, и `created_at` их не различает.

def test_the_newest_document_comes_first_when_asked_for_the_latest(session, monkeypatch):
    _health_and_vector_off(monkeypatch)
    ingest_text(session, domain="health",
                text="Дата 07.10.2023\nБиохимический анализ. Холестерин общий 6,2 ммоль/л.",
                original_filename="старый.pdf")
    ingest_text(session, domain="health",
                text="Дата 25.08.2026\nЛипидный профиль. Холестерин общий 8,4 ммоль/л.",
                original_filename="свежий.pdf")
    session.flush()

    seen = {}

    def fake_synthesis(question, fragments, **_):
        seen["fragments"] = fragments
        return Synthesis(answered=True, text="Холестерин 8,4 ммоль/л.", used=(1,))

    monkeypatch.setattr(probe_module, "synthesize_or_none", fake_synthesis)
    result = probe(session, query="какой у меня холестерин был в последний раз")

    assert "25.08.2026" in seen["fragments"][0], "первым в синтез ушёл не самый свежий документ"
    assert result.sources[0]["original_filename"] == "свежий.pdf"


def test_the_answer_names_the_date_of_its_source(session, monkeypatch):
    """Владелец должен видеть, к какому числу относится значение, не
    открывая документ."""
    _health_and_vector_off(monkeypatch)
    ingest_text(session, domain="health",
                text="Дата 25.08.2026\nЛипидный профиль. Холестерин общий 8,4 ммоль/л.",
                original_filename="свежий.pdf")
    session.flush()
    monkeypatch.setattr(probe_module, "synthesize_or_none",
                        lambda q, f, **_: Synthesis(answered=True, text="Холестерин 8,4.", used=(1,)))

    result = probe(session, query="какой у меня холестерин")

    assert "25.08.2026" in result.answer_text


def test_a_document_without_a_date_is_not_dressed_up_as_dated(session, monkeypatch):
    """«Дату определить не удалось» — это ответ, а не повод подставить
    дату загрузки."""
    _health_and_vector_off(monkeypatch)
    ingest_text(session, domain="health",
                text="Липидный профиль без даты. Холестерин общий 8,4 ммоль/л.",
                original_filename="без-даты.pdf")
    session.flush()
    seen = {}

    def fake_synthesis(question, fragments, **_):
        seen["fragments"] = fragments
        return Synthesis(answered=True, text="Холестерин 8,4.", used=(1,))

    monkeypatch.setattr(probe_module, "synthesize_or_none", fake_synthesis)
    result = probe(session, query="какой у меня холестерин")

    assert "дата документа неизвестна" in seen["fragments"][0]
    assert result.answer_text.count("(") == 0 or "неизвестна" not in result.answer_text


def test_a_consultation_quoting_an_older_analysis_is_not_the_latest(session, monkeypatch):
    """Разбор живого ответа 07.09.2026: сортировка по дате документа
    выбирала консультацию от 25.08, которая ПЕРЕСКАЗЫВАЕТ анализ от
    07.10.2023, вместо самого свежего анализа. Дата сведений и дата
    документа — разные вещи."""
    _health_and_vector_off(monkeypatch)
    ingest_text(session, domain="health",
                text=("Дата 25.08.2026\nОсмотр гастроэнтеролога. Приём от 07.10.2023: "
                      "липидный профиль, холестерин общий 6,2 ммоль/л."),
                original_filename="консультация.pdf")
    ingest_text(session, domain="health",
                text="Дата 23.08.2026\nЛипидный профиль расширенный. Холестерин общий 8,4 ммоль/л.",
                original_filename="анализ.pdf")
    session.flush()

    seen = {}

    def fake_synthesis(question, fragments, **_):
        seen["fragments"] = fragments
        return Synthesis(answered=True, text="Холестерин 8,4 ммоль/л.", used=(1,))

    monkeypatch.setattr(probe_module, "synthesize_or_none", fake_synthesis)
    result = probe(session, query="какой у меня холестерин был в последний раз")

    assert "8,4" in seen["fragments"][0], "первым ушёл пересказ старого анализа"
    assert result.sources[0]["original_filename"] == "анализ.pdf"


def test_the_fragment_shows_both_dates_when_they_differ(session, monkeypatch):
    """Модель обязана видеть, что сведения старше своего документа —
    иначе она выберет по обложке."""
    _health_and_vector_off(monkeypatch)
    ingest_text(session, domain="health",
                text=("Дата 25.08.2026\nОсмотр гастроэнтеролога. Приём от 07.10.2023: "
                      "липидный профиль, холестерин общий 6,2 ммоль/л."),
                original_filename="консультация.pdf")
    session.flush()
    seen = {}

    def fake_synthesis(question, fragments, **_):
        seen["fragments"] = fragments
        return Synthesis(answered=True, text="Холестерин 6,2.", used=(1,))

    monkeypatch.setattr(probe_module, "synthesize_or_none", fake_synthesis)
    probe(session, query="какой у меня холестерин")

    assert "данные от 07.10.2023" in seen["fragments"][0]
    assert "документ от 25.08.2026" in seen["fragments"][0]


# ── Прогон 422: записанное владельцем обязано доходить до ответа ────────

def test_vector_is_asked_even_when_lexical_filled_the_quiver(session, monkeypatch):
    """Прежнее `if len(evidence) < MAX_EVIDENCE` экономило один вызов
    embed-сервиса и ровно этим делало совпадение слов обязательным
    условием ответа. Живой замер: на «в каком порядке я всё делаю по
    прилёте» лексика вернула пять медицинских PDF с одинаковым рангом,
    колчан был «полон», а ответ лежал в векторной ветке, которую решили
    не спрашивать."""
    same_topic = _one_hot_embedding(0)
    monkeypatch.setattr(chunking_module, "embed_texts_or_none",
                        lambda texts: [same_topic for _ in texts])
    asked: list[str] = []

    def spy(texts):
        asked.extend(texts)
        return [same_topic for _ in texts]
    monkeypatch.setattr(probe_module, "embed_texts_or_none", spy)

    # Шесть документов с одним и тем же общим словом — лексика наберёт
    # полный колчан и ничего не различит.
    for i in range(6):
        ingest_text(session, domain="engineering",
                    text=f"Решение номер {i}: используем Postgres в проекте.")
    session.flush()

    probe(session, query="решение по проекту")

    assert asked, "вектор не спросили, хотя лексика ничего не различила"


def test_an_undated_note_does_not_lose_a_rank_tie(session):
    """Своя запись без даты документа не должна уступать пятёрку
    медицинским PDF только потому, что у тех дата есть. Живой прогон
    422: одиннадцать кандидатов с рангом 0.01520, среди них запись
    владельца — и она уходила в хвост."""
    from datetime import date as _date

    from helm_core.knowledge.probe import Evidence, _tiebreak_freshness

    note = Evidence(chunk_id="a", source_id="s1", chunk_text="загранпаспорт до марта",
                    original_filename=None, rank=0.0152)
    pdf = Evidence(chunk_id="b", source_id="s2", chunk_text="эндоскопия",
                   original_filename="Эндоскопия.pdf", rank=0.0152)
    pdf.content_date = _date(2026, 8, 22)

    order = sorted([pdf, note], key=_tiebreak_freshness, reverse=True)

    assert order[0] is note, "недатированная запись не может считаться самой старой"
