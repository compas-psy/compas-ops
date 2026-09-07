"""Операции над найденным (распоряжение владельца 07.09.2026, п.3).

Здесь проверяется именно ИСПОЛНЕНИЕ: «сколько» обязано вернуть число,
рассчитанное по набору, а не пересказ; «все» — обойти набор целиком либо
прямо сказать о неполноте. Модель в этих операциях не участвует, поэтому
и тесты детерминированные, без подмены синтеза.
"""

from sqlalchemy import select

from helm_core.knowledge import probe as probe_module
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.operations import (
    OP_COUNT, OP_ENUMERATE, OP_VALUE, detect_operation, find_enumeration,
    run_count, run_enumerate,
)
from helm_core.knowledge.probe import probe
from helm_core.models import KnowledgeAnswerRun

KIT = "В дорожную аптечку кладу: ибупрофен, лоперамид, пластырь и антисептик."


# ── Какая операция запрошена ───────────────────────────────────────────

def test_the_operation_is_read_from_the_request_shape():
    assert detect_operation("Сколько всего пунктов в аптечке?") == OP_COUNT
    assert detect_operation("перечисли все мои каналы") == OP_ENUMERATE
    assert detect_operation("какой у меня холестерин") == OP_VALUE


def test_counting_wins_over_listing_when_both_words_are_there():
    """«Сколько ВСЕГО пунктов» — это счёт, хотя «всего» тоже слово
    перечисления."""
    assert detect_operation("Сколько всего пунктов?") == OP_COUNT


# ── Граница списка важнее разделителей ─────────────────────────────────

def test_a_list_after_a_colon_is_counted():
    found = find_enumeration("сколько пунктов в аптечке", KIT)

    assert found is not None and found.count == 4


def test_a_sentence_with_a_lead_in_is_not_counted():
    """Первый замер дал «насчитал 5» на «Запомнив дорожную аптечку, я
    кладу ибопрофен, лаперамид, пластырь и антисептик»: пунктов четыре, а
    пятым посчиталась вводная часть. Уверенное неправильное число —
    ровно тот дефект, который закрывался всю неделю."""
    spoken = ("Запомнив дорожную аптечку, я кладу ибопрофен, лаперамид, "
              "пластырь и антисептик.")

    assert find_enumeration("сколько пунктов в аптечке", spoken) is None


def test_a_bare_list_without_a_lead_in_is_counted():
    found = find_enumeration("сколько лекарств",
                             "Лекарства: ибупрофен, лоперамид, пластырь, антисептик.")

    assert found is not None and found.count == 4


def test_a_list_the_question_does_not_touch_is_refused():
    """Поиск отбирает документ, но не предложение внутри него: без
    общего слова с вопросом перечисление считать нельзя (прогон 427)."""
    assert find_enumeration("сколько лекарств",
                            "Ибупрофен, лоперамид, пластырь, антисептик.") is None


def test_counting_refuses_instead_of_guessing():
    assert run_count("сколько пунктов",
                     ["Просто предложение без перечисления."]) is None


def test_listing_says_when_it_showed_not_everything():
    answer = run_enumerate("перечисли аптечку", [KIT], complete=False)

    assert answer is not None
    assert "Показал не всё" in answer.text

    complete = run_enumerate("перечисли аптечку", [KIT], complete=True)
    assert "Показал не всё" not in complete.text


# ── Общий путь: операция управляет ответом ─────────────────────────────

def test_count_is_computed_over_the_corpus_not_retold(session, monkeypatch):
    """Число получено пересчётом, модель не звалась вовсе."""
    monkeypatch.setattr(probe_module, "synthesize_or_none",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("счёт не должен звать модель")))
    ingest_text(session, domain="personal", text=KIT, original_filename="аптечка.md")
    session.flush()

    result = probe(session, query="сколько пунктов в дорожной аптечке")

    assert result.outcome == "LOCAL_ANSWER"
    assert "Насчитал 4" in result.answer_text
    assert "аптечка.md" in result.answer_text, "ответ без источника непроверяем"
    assert session.scalars(select(KnowledgeAnswerRun)).all(), "прогон не записан"


def test_a_remembered_list_is_counted_not_recited(session):
    """Распоряжение п.2: ранний возврат заметки снят там, где спросили не
    её саму. На «сколько» дословный текст ответом не является."""
    from helm_core.knowledge.memory import try_remember

    try_remember(session, channel="telegram",
                 text=f"Запомни: {KIT}", vault_root="/tmp/vault-test")
    session.flush()

    result = probe(session, query="сколько пунктов в дорожной аптечке")

    assert result.outcome == "LOCAL_ANSWER"
    assert "Насчитал 4" in result.answer_text


def test_asking_for_the_value_still_returns_the_note_verbatim(session):
    """Обратная сторона: байтовая точность сохранённого значения —
    принятое владельцем поведение, и операция «значение» его сохраняет."""
    from helm_core.knowledge.memory import try_remember

    try_remember(session, channel="telegram",
                 text="Запомни ссылку на канал B17: https://www.b17.ru/eliah/",
                 vault_root="/tmp/vault-test")
    session.flush()

    result = probe(session, query="дай ссылку на мой канал B17")

    assert "https://www.b17.ru/eliah/" in result.answer_text


# ── Прогон 427: чужое перечисление не считается ────────────────────────

def test_a_list_from_an_unrelated_text_is_not_counted():
    """Живой прогон 427 поймал регрессию в этой же операции через час
    после её выката: на «сколько у меня каналов» пришло «Насчитал 6» по
    фразе из книги Линде «В результате двухлетней терапии она сказала:
    „У меня, конечно, остались проблемы…"». Двоеточие и запятые сделали
    цитату «списком», а совпадение по слову «меня» — «релевантной»."""
    quote = ("В результате двухлетней терапии она сказала: «У меня, конечно, "
             "остались проблемы, но я вспоминаю, какой я была, это просто ужас!»")

    assert find_enumeration("сколько у меня каналов", quote) is None


def test_a_matching_list_is_still_counted():
    channels = "Ссылки на мои каналы: Telegram, B17, VK, Дзен."
    found = find_enumeration("сколько у меня каналов", channels)

    assert found is not None and found.count == 4
