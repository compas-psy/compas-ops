"""Операции над найденным (распоряжение владельца 07.09.2026, п.3).

Здесь проверяется именно ИСПОЛНЕНИЕ: «сколько» обязано вернуть число,
рассчитанное по набору, а не пересказ; «все» — обойти набор целиком либо
прямо сказать о неполноте. Модель в этих операциях не участвует, поэтому
и тесты детерминированные, без подмены синтеза.
"""

import uuid

from sqlalchemy import select

from helm_core.knowledge import probe as probe_module
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.operations import (
    OP_COMPARE, OP_COUNT, OP_DEFINE, OP_ENUMERATE, OP_EXAMPLE, OP_VALUE,
    comparison_sides, detect_operation, find_enumeration,
    missing_comparison_side, run_count, run_enumerate, run_measurements,
    select_for_operation,
)
from helm_core.knowledge.probe import probe
from helm_core.knowledge.synthesis import Synthesis
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeAnswerRun


def _lexical_of(module):
    """Настоящая лексическая ветка до подмены — фикстура должна
    находиться ею же, иначе тест проверяет собственную выдумку."""
    return module._lexical_search

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


# ── «сколько» по нескольким источникам ────────────────────────────────
#
# Разбор владельца 07.09.2026: run_count() возвращал первое найденное
# перечисление. Это закрывало подсчёт по одной заметке и не закрывало
# общий вопрос — врачи из двух выписок считались по одной из них, а
# число приходило уверенное.

#: По три пункта в каждой записи: меньше трёх `_items_of()` списком не
#: считает — граница перечисления должна быть видна, а не угадана.
DOCTORS = ["В марте посещал врачей: Петров, Сидорова, Кузнецов.",
           "В июне посещал врачей: Сидорова, Кузнецов, Волкова."]


def test_count_merges_every_matching_source():
    answer = run_count("сколько врачей я посещал", DOCTORS)
    assert answer is not None
    assert "Насчитал 4" in answer.text, answer.text
    assert answer.used == (1, 2), "источник половины подсчёта потерян"


def test_count_removes_duplicates_by_entity_not_by_string():
    """«Сидорова» и «Кузнецов» повторяются в обеих записях.

    Строгое сравнение строк дало бы шесть врачей вместо четырёх.
    """
    answer = run_count("сколько врачей я посещал", DOCTORS)
    assert "Насчитал 6" not in answer.text


def test_a_fuller_record_of_the_same_entity_wins():
    fragments = ["Посещал врачей: Петров, Сидорова, Волкова.",
                 "Посещал врачей: врач Петров Иван, Кузнецов, Волкова."]
    answer = run_count("сколько врачей", fragments)
    assert "Насчитал 4" in answer.text, answer.text
    assert "Петров Иван" in answer.text, "осталась менее полная запись сущности"


def test_a_single_source_answer_keeps_its_verbatim_quote():
    answer = run_count("сколько врачей я посещал", [DOCTORS[0]])
    assert "Дословно из записи" in answer.text
    assert answer.used == (1,)


def test_no_enumeration_anywhere_is_still_an_honest_none():
    assert run_count("сколько врачей", ["Погода была хорошая."]) is None


def test_completeness_is_decided_by_retrieval_not_by_what_survived_filtering(
        session, monkeypatch):
    """Полнота решается ДО фильтрации — по тому, упёрся ли поиск в лимит.

    Разбор владельца 07.09.2026: «полноту нельзя определять только
    числом кандидатов после фильтрации». Прежний признак —
    `len(candidates) < CANDIDATE_LIMIT`, где candidates это остаток
    ПОСЛЕ отбраковки `is_quotable` и переупорядочивания.

    Ситуация ставится прямо: ветка поиска возвращает ровно свой лимит,
    и почти всё в ней нецитируемо (меньше четырёх слов — шапки, метки).
    До подсчёта доходят два фрагмента. Прежний признак назвал бы такой
    ответ полным, потому что смотрел на эти два, а не на пятнадцать.
    """
    from helm_core.knowledge.probe import CANDIDATE_LIMIT, Evidence

    ingest_text(session, domain="personal", text=KIT, original_filename="аптечка.md")
    session.flush()
    # Привязка тенанта — как это делает сам probe: без неё RLS не пустит
    # прямой вызов ветки, и тест проверял бы пустоту.
    tenant = bind_knowledge_user(session, None)
    real = _lexical_of(probe_module)(session, query="все пункты дорожной аптечки",
                                     domain=None, knowledge_user_id=tenant, source_ids=())
    assert real, "фикстура не нашлась лексикой — тест проверял бы не то"

    padding = [Evidence(chunk_id=str(uuid.uuid4()), source_id=real[0].source_id,
                        chunk_text=f"Метка {i}", original_filename="метка.md",
                        rank=0.001)
               for i in range(CANDIDATE_LIMIT - len(real))]
    monkeypatch.setattr(probe_module, "_lexical_search",
                        lambda *a, **kw: list(real) + padding)

    result = probe(session, query="все пункты дорожной аптечки")

    assert result.outcome == "LOCAL_ANSWER"
    assert "Показал не всё" in result.answer_text, (
        "поиск упёрся в лимит, а ответ выдан за полный")


def test_a_corpus_that_fits_is_not_declared_incomplete(session):
    """Вторая половина того же правила: лишней оговорки быть не должно."""
    ingest_text(session, domain="personal", text=KIT, original_filename="аптечка.md")
    session.flush()

    result = probe(session, query="все пункты дорожной аптечки")

    assert result.outcome == "LOCAL_ANSWER"
    assert "Показал не всё" not in result.answer_text


# ── ТИП СВЕДЕНИЙ: СРАВНЕНИЕ ─────────────────────────────────────────
#
# Распоряжение владельца 07.09.2026, п.3: «Для определения, сравнения
# или примера проверяй, что источник действительно содержит требуемый
# тип сведений». Определение закрыто `undefined_subjects`, пример —
# отбором формы. Сравнение до этой правки не распознавалось операцией
# вовсе: «чем отличается А от Б» шло как обычный вопрос о значении.

STRAHOVKA = (
    "Страховка поездки. Страховщик Бета, полис 77-1234, "
    "стоимость 4300 рублей, срок до 30.09.2026."
)


def test_comparison_is_its_own_operation():
    assert detect_operation("чем отличается страховка поездки от страховки квартиры") \
        == OP_COMPARE
    assert detect_operation("сравни страховку поездки и страховку квартиры") == OP_COMPARE
    assert detect_operation("в чём разница между полисом и договором") == OP_COMPARE
    # Определение сравнением не становится.
    assert detect_operation("что такое страховка поездки") == OP_DEFINE


def test_comparison_sides_are_split_by_connector():
    assert comparison_sides("чем отличается страховка поездки от страховки квартиры") \
        == ("страховка поездки", "страховки квартиры")
    # Сторон не видно — и придумывать их нечем.
    assert comparison_sides("сравни эти документы") is None
    assert comparison_sides("какой у меня был холестерин") is None


def test_missing_side_is_named(session):
    """Сторона, которой в найденном нет ни одним словом, названа."""
    absent = missing_comparison_side(
        "чем отличается страховка поездки от страховки квартиры", [STRAHOVKA])
    assert absent == "страховки квартиры"


def test_present_side_is_not_reported_missing():
    """Правило намеренно слабое: совпало хоть одно слово — сторона есть."""
    assert missing_comparison_side(
        "чем отличается страховка поездки от страховки квартиры",
        [STRAHOVKA, "Страховка квартиры. Страховщик Альфа."]) is None


def test_probe_says_which_side_is_missing(session):
    """Живой путь: сравнивать не с чем — и владелец видит, чего нет.

    Проверяется именно ЭТОТ исход, а не «ничего не нашёл»: записи по
    первой стороне есть, и отправлять владельца искать всё сравнение
    заново было бы неправдой о состоянии памяти.
    """
    ingest_text(session, domain="personal", text=STRAHOVKA,
                original_filename="страховка-поездки.md")
    session.flush()

    result = probe(session,
                   query="чем отличается страховка поездки от страховки квартиры")

    assert result.outcome == "LOCAL_NOT_FOUND"
    assert "Сравнить не могу" in result.answer_text
    assert "квартиры" in result.answer_text
    assert not result.answer_text.startswith("Ответа на этот вопрос")


# ── ТИП СВЕДЕНИЙ: ПРИМЕР ────────────────────────────────────────────
#
# У определения проверка на выходе есть с прогона 445
# (`synthesis.undefined_subjects`): ответ, поданный как определение,
# засчитывается, только если то же определяемое определяет и источник.
# У примера такой проверки не было — только примечание, которое ничему
# не мешало. Здесь она и закрывается, ТЕМ ЖЕ способом: судят по
# утверждению ответа, а не по тому, какие слова нашлись в источнике.
#
# Почему не входным запретом («формы нет — модель не зовём»): он
# отбрасывал бы и законные ответы. «Эмоционально-образная терапия
# работает с образом чувства» отвечает на «что такое ЭОТ», хотя
# определительного оборота в нём нет; тест
# `test_a_source_named_in_the_question_confines_the_search` держит
# ровно этот случай.

MENTION = "Работа с неуверенностью описана в третьей главе книги подробно."
EXAMPLED = ("Например, клиентка представила свою неуверенность "
            "в виде серого тумана над головой.")


def _fake_synthesis(monkeypatch, text):
    monkeypatch.setattr(
        probe_module, "synthesize_or_none",
        lambda *a, **kw: Synthesis(answered=True, text=text, used=(1,)))


def test_an_example_absent_from_sources_is_rejected(session, monkeypatch):
    """Пример спрошен, в источниках примера нет, а ответ подан как пример."""
    ingest_text(session, domain="personal", text=MENTION,
                original_filename="конспект.md")
    session.flush()
    _fake_synthesis(monkeypatch, "Например, автор советует начать с малого.")

    result = probe(session, query="дай примеры работы с неуверенностью")

    assert result.outcome == "LOCAL_NOT_FOUND"
    # Отклонение, а не «данных нет»: записи есть, подтвердить не смогли.
    assert "подтвердить ответ по ним не смог" in result.answer_text


def test_an_example_present_in_sources_is_kept(session, monkeypatch):
    """Вторая половина правила: пример в источнике есть — ответ проходит."""
    ingest_text(session, domain="personal", text=EXAMPLED,
                original_filename="разбор.md")
    session.flush()
    _fake_synthesis(monkeypatch, "Например, неуверенность предстала серым туманом.")

    result = probe(session, query="дай примеры работы с неуверенностью")

    assert result.outcome == "LOCAL_ANSWER"
    assert "серым туманом" in result.answer_text


def test_the_word_example_outside_an_example_question_is_left_alone(session, monkeypatch):
    """«Например» в пересказе — оборот речи, а не утверждение о типе.

    Проверка привязана к СПРОШЕННОМУ примеру намеренно: без этого
    условия под неё попал бы любой ответ, где модель перечисляет через
    «например», и правило отбирало бы законные ответы.
    """
    ingest_text(session, domain="personal", text=MENTION,
                original_filename="конспект.md")
    session.flush()
    _fake_synthesis(monkeypatch, "Например, третья глава книги.")

    result = probe(session, query="где описана работа с неуверенностью")

    assert result.outcome == "LOCAL_ANSWER"


# --- Несколько строк одного семейства (прогоны 494, 496) ----------------
#
# После починки разбора таблиц запись владельца от 23.08.2026 несёт
# четыре строки холестерина. Модель выбрала одну и ошиблась (ЛПНП
# вместо общего); проверка принадлежности её остановила, и владелец
# получил отказ — при том что число лежит в его записях и читается
# дословно. Выбирать за него нечем, показать всё — можно.

LIPID_ROWS = ("Холестерин общий: ↑ 8.4 ммоль/л (см. комментарий)\n"
              "Холестерин-ЛПВП (липопротеины высокой плотности): 1.77 ммоль/л\n"
              "Холестерин-ЛПНП (липопротеины низкой плотности): ↑ 5.7 ммоль/л\n"
              "Триглицериды: 1.04 ммоль/л")


def test_every_matching_measurement_row_is_shown():
    done = run_measurements("какой у меня был холестерин в последний раз?",
                            [LIPID_ROWS])
    assert done is not None
    assert "8.4 ммоль/л" in done.text
    assert "5.7 ммоль/л" in done.text
    assert "1.77 ммоль/л" in done.text


def test_rows_about_something_else_are_left_out():
    """Триглицериды измерены в той же таблице и к вопросу не относятся."""
    done = run_measurements("какой у меня был холестерин?", [LIPID_ROWS])
    assert done is not None
    assert "Триглицериды" not in done.text


def test_a_single_row_is_left_to_the_synthesiser():
    """Одна подходящая строка — не выписка, а обычный связный ответ."""
    assert run_measurements("какой холестерин",
                            ["Холестерин общий: 6.2 ммоль/л"]) is None


def test_lines_without_a_measurement_are_not_rows():
    """Упоминание без числа с единицей строкой таблицы не является."""
    text = ("Жалобы: на повышение холестерина\n"
            "Диета: гипохолестериновая\n"
            "Рекомендован расчет риска по шкале SCORE")
    assert run_measurements("какой у меня холестерин", [text]) is None


def test_the_answer_says_plainly_that_it_did_not_choose():
    """Выписка не выдаётся за ответ на вопрос: сказано, что выбора не было."""
    done = run_measurements("какой у меня был холестерин?", [LIPID_ROWS])
    assert "по вопросу не видно" in done.text
