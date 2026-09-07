"""Локальный синтез ответа из нескольких фрагментов (P4).

Сама модель здесь не участвует: проверяется разбор её ответа и —
главное — заземление, то есть по каким фрагментам ответ реально собран.
Качество модели меряется живьём, а не в unit-тесте.
"""

from helm_core.knowledge.synthesis import (build_prompt, grounded_fragments,
                                          parse_response, unbound_measurements,
                                          ungrounded_numbers)

CHANNELS = ("ссылки на мои каналы:\n"
            "Telegram: https://t.me/ilyamartynov_yourway\n"
            "B17.ru: https://www.b17.ru/eliah/")
TEA = "ссылку на видео про чай: https://www.facebook.com/share/r/1BuuXvqbyH/"
ENDOSCOPY = "Эзофагогастродуоденоскопия выполнена под местной анестезией."
FRAGMENTS = [CHANNELS, TEA, ENDOSCOPY]


def test_answer_without_the_format_line_is_still_grounded():
    """Живой прогон 388: на «Дай ссылку на мой канал B17» модель ответила
    по делу, но строку со ссылками не написала — и ответ отбрасывался
    целиком, а владелец получал «ближайшую цитату» про чай."""
    result = parse_response("Ссылка на ваш канал B17: https://www.b17.ru/eliah/",
                            fragments=FRAGMENTS)

    assert result is not None and result.answered
    assert result.used == (1,)


def test_model_reference_to_a_fragment_it_did_not_use_is_dropped():
    """Ссылки модели — её утверждение о себе, а не проверка. Решает
    содержание: пересечение непусто — берётся оно."""
    result = parse_response(
        "Ссылка: https://www.b17.ru/eliah/\nФРАГМЕНТЫ: 1,3", fragments=FRAGMENTS)

    assert result.used == (1,)


def test_answer_resting_on_nothing_shown_is_refused():
    """Проверить такой ответ нечем — показывать его нельзя. И это не
    «модели не было»: фрагменты прочитаны, ответа в них нет."""
    rejected = parse_response("Общие рассуждения без единого совпадения",
                              fragments=FRAGMENTS)
    assert rejected is not None and not rejected.answered


def test_no_answer_is_an_outcome_not_a_failure():
    result = parse_response("НЕТ ОТВЕТА", fragments=FRAGMENTS)

    assert result is not None and result.answered is False


def test_one_accidental_word_does_not_outweigh_the_real_source():
    """Ответ про каналы совпадает с первым фрагментом двумя опорными
    словами и с чаем — одной случайной словоформой. Источником должен
    остаться первый."""
    answer = "Ваш канал B17: https://www.b17.ru/eliah/, ссылку смотрите там"

    assert grounded_fragments(answer, [CHANNELS, TEA]) == (1,)


def test_prompt_shows_every_fragment_numbered():
    prompt = build_prompt("какие у меня каналы", FRAGMENTS)

    assert "[1]" in prompt and "[2]" in prompt and "[3]" in prompt
    assert "какие у меня каналы" in prompt


# ── число в ответе обязано быть в источнике ──────────────────────────────
#
# Живой ответ владельцу 07.09.2026: «Какой у меня холестерин был в
# последний раз?» → «8.1 ммоль/л» с честно названным источником, в
# котором этого значения нет вовсе (разведка 396: ближайшее «Холестерин
# 6.2» от 07.10.2023). Заземление пропустило: «8.1» короче четырёх
# символов и словом не считалось — самая ответственная часть ответа не
# проверялась ничем.

ANALYSIS = ["Холестерин общий 6,2 ммоль/л", "Давление 120/80 мм рт. ст."]


def test_a_value_that_is_not_in_the_evidence_is_refused():
    rejected = parse_response("Уровень холестерина 8.1 ммоль/л.", fragments=ANALYSIS)
    assert rejected is not None and not rejected.answered


def test_the_same_value_written_with_a_comma_counts_as_present():
    result = parse_response("Уровень холестерина 6.2 ммоль/л.", fragments=ANALYSIS)

    assert result is not None and result.answered


def test_a_compound_value_is_compared_whole():
    assert ungrounded_numbers("Давление 120/80.", ANALYSIS) == set()
    assert ungrounded_numbers("Давление 130/90.", ANALYSIS) == {"130/90"}


def test_a_year_may_be_named_even_if_the_document_writes_it_shortly():
    """«25.08.26» во фрагменте и «в 2026 году» в ответе — не выдумка."""
    assert ungrounded_numbers("Осмотр был в 2026 году", ["Дата: 25.08.26"]) == set()


# ── Живой прогон 417: три канала выдумки, каждый с ценой ────────────────

def test_our_own_date_signature_is_not_evidence_for_itself():
    """probe подписывает фрагмент датой документа, чтобы модель могла
    отвечать «в последний раз». Живой ответ 07.09.2026: на «до какого
    числа действует загран» пришло «до 22.08.2026» с источником
    «Эндоскопия.pdf» — модель взяла дату из НАШЕЙ приписки, а проверка
    сверила ответ с ней же. Дата обязана проверяться по исходному тексту.
    """
    shown = ["(документ от 22.08.2026) Эзофагогастродуоденоскопия выполнена."]
    sources = ["Эзофагогастродуоденоскопия выполнена."]

    rejected = parse_response("Загранпаспорт действует до 22.08.2026.",
                              fragments=shown, sources=sources)
    assert rejected is not None and not rejected.answered
    # Без разделения тот же ответ проходил — вот цена смешения.
    assert parse_response("Загранпаспорт действует до 22.08.2026.",
                          fragments=shown) is not None


def test_a_name_that_is_not_in_the_evidence_is_refused():
    """«ибопрофен» во фрагменте, «ibuprofene» в ответе (живой ответ на
    «сколько пунктов в аптечке»). Проверка чисел букв не смотрела."""
    kit = ["я кладу ибопрофен, лаперамид, пластырь и антисептик"]

    rejected = parse_response("В аптечке: ibuprofene, лаперамид, пластырь.", fragments=kit)
    assert rejected is not None and not rejected.answered
    result = parse_response("В аптечке: ибопрофен, лаперамид, пластырь.", fragments=kit)
    assert result is not None and result.answered


def test_a_name_from_the_evidence_survives_declension():
    """Заземление по корню, а не по точному совпадению: иначе любой падеж
    считался бы выдумкой."""
    booking = ["билеты я бронирую через Аэрофлот, а страховку в Ингосстрахе"]
    result = parse_response("Билеты — в Аэрофлоте, страховка — Ингосстрах.",
                            fragments=booking)

    assert result is not None and result.answered


def test_ordinary_words_are_not_required_to_be_in_the_evidence():
    """Связки — не содержание: требовать их в источнике значило бы
    запретить синтез как таковой."""
    analysis = ["Холестерин общий 6,2 ммоль/л"]
    result = parse_response("Уровень холестерина составляет 6.2 ммоль/л.",
                            fragments=analysis)

    assert result is not None and result.answered


def test_the_citation_marker_is_cut_wherever_it_stands():
    """Живой ответ: «…до 16:48 23.08.2026. ФРАГМЕНТЫ: 2» — маркер в конце
    предложения уезжал в мессенджер как часть ответа."""
    result = parse_response("Давление 120/80. ФРАГМЕНТЫ: 2", fragments=ANALYSIS)

    assert result is not None
    assert "ФРАГМЕНТЫ" not in result.text
    assert result.text == "Давление 120/80"
    assert result.used == (2,)


def test_an_unparsed_marker_refuses_the_answer():
    """Разметку, которую мы не узнали, показывать нельзя: неизвестно, что
    ещё в тексте не ответ."""
    rejected = parse_response("Давление 120/80. ФРАГМЕНТЫ: нет", fragments=ANALYSIS)
    assert rejected is not None and not rejected.answered


def test_a_rejected_answer_is_not_the_same_as_no_model():
    """Живой прогон 419: заземление отбросило выдуманный срок, probe
    откатился на ближайшую цитату — и на «до какого числа действует
    загран» пришли «Антитела к фактору Кастла». Отброшенный ответ
    обязан читаться как «прочитал, ответа нет», а не как «модели не
    было»: во втором случае откат на цитату честен, в первом — нет."""
    rejected = parse_response("Срок до 01.01.2030.", fragments=ANALYSIS)

    assert rejected is not None, "не None — иначе probe уйдёт в откат на цитату"
    assert not rejected.answered
    assert rejected.text == ""


# ── Окно фрагмента вокруг вопроса (распоряжение 07.09.2026, п.2) ────────

def test_the_window_follows_the_question_not_the_first_characters():
    """Механическая обрезка отбрасывала ровно то, что искали: в
    лабораторном бланке первые сотни символов — шапка учреждения и номер
    заказа, а значение с единицей стоит ниже."""
    from helm_core.knowledge.synthesis import relevant_window

    blank = ("АО «Медси»\nНомер заказа 1011794275\n"
             + "строка шапки бланка\n" * 40
             + "Холестерин общий 8.4 ммоль/л\n")

    window = relevant_window("какой у меня холестерин", blank, width=200)

    assert "Холестерин общий 8.4 ммоль/л" in window
    assert "Номер заказа" not in window


def test_a_short_fragment_is_not_touched():
    from helm_core.knowledge.synthesis import relevant_window

    assert relevant_window("что угодно", " Холестерин 6.2 ") == "Холестерин 6.2"


def test_a_fragment_without_question_words_keeps_its_beginning():
    """Ни одно слово вопроса не встретилось — обрезка остаётся прежней,
    выдумывать «релевантное» место не из чего."""
    from helm_core.knowledge.synthesis import relevant_window

    text = "первая строка\n" + "прочее содержание\n" * 40

    assert relevant_window("совсем другая тема", text, width=50).startswith("первая строка")


# ── п.4 распоряжения 07.09.2026: проверка утверждений ────────────────────

def test_four_digit_number_is_checked_like_any_other():
    """Прежнее правило прощало ЛЮБОЕ четырёхзначное число как «год», и
    через эту дыру проходила дозировка."""
    assert ungrounded_numbers("Принимать 1000 мг", ["Принимать 500 мг"]) == {"1000"}


def test_year_is_forgiven_only_when_the_source_names_it():
    assert ungrounded_numbers("Приём был в 2026 году", ["Осмотр 25.08.26"]) == set()
    assert ungrounded_numbers("Родился в 1998 году", ["Осмотр 25.08.26"]) == {"1998"}


#: Живой корпус владельца, прогон 437 — дословно.
CHOLESTEROL = "07.10.2023  Липидный профиль (ммоль/л) Холестерин общий: 6.2"
ERYTHROCYTES = ("07.10.2023  Гематологические исследования (InterSystem) СОЭ: 4 (0-15), "
                "(RBC) Эритроциты: 5.45 (4.3-5.7),")


def test_value_belonging_to_another_measure_is_not_grounding():
    """Прогон 435 ответил «5.7 ммоль/л» на вопрос о холестерине. Число во
    фрагментах было — верхней границей нормы эритроцитов."""
    assert unbound_measurements("Уровень холестерина 5.7 ммоль/л",
                                [CHOLESTEROL, ERYTHROCYTES]) == {"5.7 ммоль/л"}


def test_the_real_value_passes_the_binding_check():
    assert unbound_measurements("Уровень холестерина 6.2 ммоль/л",
                                [CHOLESTEROL, ERYTHROCYTES]) == set()


def test_label_on_the_line_above_still_binds_the_value():
    """Лабораторный бланк из PDF часто разложен на две строки."""
    assert unbound_measurements("Холестерин общий 6.2 ммоль/л",
                                ["Холестерин общий\n6.2 ммоль/л"]) == set()


def test_a_value_does_not_borrow_the_label_of_a_neighbouring_table_row():
    fragment = "Холестерин общий: 6.2\nЭритроциты: 5.45"
    assert unbound_measurements("Уровень холестерина 5.45 ммоль/л", [fragment]) \
        == {"5.45 ммоль/л"}


def test_rejected_answer_is_marked_unverified_not_absent():
    """«Отклонили» и «в документах нет» — разные исходы (п.4)."""
    rejected = parse_response("Уровень холестерина 5.7 ммоль/л ФРАГМЕНТЫ: 2",
                              fragments=[CHOLESTEROL, ERYTHROCYTES])
    assert rejected.answered is False
    assert rejected.verified is False

    honest = parse_response("НЕТ ОТВЕТА", fragments=[CHOLESTEROL])
    assert honest.answered is False
    assert honest.verified is True


def test_an_answer_built_from_two_documents_names_both():
    """Прежде оставался только сильнейший фрагмент, и половина
    происхождения ответа пропадала молча."""
    assert grounded_fragments("Безручко назначила эндокринолог осмотр",
                              ["Врач: Безручко Дарья", "Направление: эндокринолог"]) \
        == (1, 2)
