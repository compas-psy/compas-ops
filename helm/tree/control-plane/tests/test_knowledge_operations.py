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
    OP_COUNT, OP_DEFINE, OP_ENUMERATE, OP_EXAMPLE, OP_VALUE, detect_operation,
    find_enumeration, run_count, run_enumerate, select_for_operation,
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


# ── список в столбик (замер 07.09.2026) ──────────────────────────────────

#: Запись владельца дословно, сокращённая до четырёх строк.
CHANNELS = """ссылки на мои каналы:
Telegram: https://t.me/ilyamartynov_yourway
Max: https://max.ru/id505003226577_biz
VC.ru: https://vc.ru/id3888317
B17.ru: https://www.b17.ru/eliah/"""


def test_a_list_written_line_by_line_is_counted():
    """Прогон 435: на «сколько у меня каналов» пришёл сам список, а не
    число. `_SENTENCE_SPLIT_RE` режет текст по переводу строки, и каждая
    строка становилась «предложением» без разделителей — верный расчёт
    отбрасывался на самой обычной форме списка."""
    answer = run_count("Сколько у меня каналов?", [CHANNELS])

    assert answer is not None
    assert "Насчитал 4" in answer.text


def test_a_colon_inside_a_sentence_does_not_start_a_column_list():
    """Граница списка — двоеточие НА КОНЦЕ строки. Иначе цитата из книги
    снова станет списком (прогон 427)."""
    quote = ("В результате двухлетней терапии она сказала: «У меня, конечно, "
             "остались проблемы, но я вспоминаю, какой я была, это просто ужас!»")

    assert run_count("Сколько у меня каналов?", [quote]) is None


# ── форма ответа решает, что попадёт в доказательства (прогон 443) ───────

def test_the_question_asks_for_a_definition_not_just_a_value():
    assert detect_operation("По книге Линде что такое эмоционально-образная терапия") \
        == OP_DEFINE
    assert detect_operation("Кто такой Линде?") == OP_DEFINE
    assert detect_operation("дай примеры того, как бороться с неуверенностью") == OP_EXAMPLE
    assert detect_operation("Какой у меня был холестерин?") == OP_VALUE


def test_a_form_word_does_not_override_counting():
    """«Сколько примеров» — это счёт, а не просьба о примерах."""
    assert detect_operation("Сколько примеров в главе?") == OP_COUNT


def test_a_defining_fragment_goes_before_a_mention():
    """Прогон 443: в доказательства попал раздел «Рекомендуемая
    литература» — там термин упомянут и не определён."""
    texts = [
        "#### Рекомендуемая литература\n1. Линде Н.Д. Эмоционально-образная терапия. М., 2011.",
        "Эмоционально-образная терапия — это метод работы с образом чувства.",
        "Глава про зависть, где терапия не упоминается вовсе.",
    ]

    selection = select_for_operation(OP_DEFINE, "что такое эмоционально-образная терапия", texts)

    assert selection.order[0] == 1
    assert selection.found is True


def test_nothing_is_thrown_away_when_the_form_is_absent():
    """Отбор, а не фильтр: определение могло стоять не там, где его ждёт
    правило, и терять из-за этого весь ответ нельзя."""
    texts = ["Упоминание ферритина без определения.", "Совсем другой текст."]

    selection = select_for_operation(OP_DEFINE, "что такое ферритин", texts)

    assert sorted(selection.order) == [0, 1]
    assert selection.found is False


def test_a_definition_of_something_else_is_not_the_answer():
    """Совпадение с вопросом обязательно — по той же причине, что в
    `find_enumeration()`: чужое определение определением к вопросу не
    становится."""
    texts = ["Ферритин — это белок, депонирующий железо."]

    selection = select_for_operation(OP_DEFINE, "что такое эмоционально-образная терапия", texts)

    assert selection.found is False


def test_operations_without_a_required_form_keep_the_search_order():
    texts = ["первый", "второй", "третий"]

    for operation in (OP_VALUE, OP_COUNT, OP_ENUMERATE):
        selection = select_for_operation(operation, "любой вопрос", texts)
        assert selection.order == (0, 1, 2)
        assert selection.found is True
