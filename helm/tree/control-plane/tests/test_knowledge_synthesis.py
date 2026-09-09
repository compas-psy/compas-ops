"""Локальный синтез ответа из нескольких фрагментов (P4).

Сама модель здесь не участвует: проверяется разбор её ответа и —
главное — заземление, то есть по каким фрагментам ответ реально собран.
Качество модели меряется живьём, а не в unit-тесте.
"""

from helm_core.knowledge.synthesis import (build_prompt, grounded_fragments,
                                          parse_response, unbound_claims,
                                          undefined_subjects,
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
    assert unbound_claims("Уровень холестерина 5.7 ммоль/л",
                                [CHOLESTEROL, ERYTHROCYTES]) == {"5.7 ммоль/л"}


def test_the_real_value_passes_the_binding_check():
    assert unbound_claims("Уровень холестерина 6.2 ммоль/л",
                                [CHOLESTEROL, ERYTHROCYTES]) == set()


def test_label_on_the_line_above_still_binds_the_value():
    """Лабораторный бланк из PDF часто разложен на две строки."""
    assert unbound_claims("Холестерин общий 6.2 ммоль/л",
                                ["Холестерин общий\n6.2 ммоль/л"]) == set()


def test_a_value_does_not_borrow_the_label_of_a_neighbouring_table_row():
    fragment = "Холестерин общий: 6.2\nЭритроциты: 5.45"
    assert unbound_claims("Уровень холестерина 5.45 ммоль/л", [fragment]) \
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


# ── утверждение проверяется целиком: объект, отношение, значение ──────
#
# Разбор владельца 07.09.2026: «Поездка: страховщик Бета» и «Квартира:
# страховщик Альфа» давали подтверждённое «Страховщик поездки Альфа».
# Прежняя проверка требовала совпадения ОДНОГО слова строки с ответом —
# слова «страховщик» хватало, и строка про квартиру подтверждала
# утверждение про поездку.

INSURANCE = ["Поездка: страховщик Бета, полис до 12.12.2026",
             "Квартира: страховщик Альфа, полис до 01.03.2027"]


def test_a_named_value_of_another_object_is_not_grounding():
    assert unbound_claims("Страховщик поездки Альфа", INSURANCE) == {"Альфа"}


def test_the_named_value_of_the_right_object_passes():
    assert unbound_claims("Страховщик поездки Бета", INSURANCE) == set()


def test_the_other_object_keeps_its_own_value():
    assert unbound_claims("Страховщик квартиры Альфа", INSURANCE) == set()
    assert unbound_claims("Страховщик квартиры Бета", INSURANCE) == {"Бета"}


def test_a_paraphrase_absent_from_the_source_does_not_reject_a_true_answer():
    """Требуются только слова, которые в источниках вообще встречаются.

    Иначе правило отвергало бы «Уровень холестерина 6.2 ммоль/л»: слова
    «уровень» в лабораторном бланке нет, а значение своё.
    """
    assert unbound_claims("По страховке для путешествия — Бета", INSURANCE) == set()


def test_one_shared_word_is_no_longer_enough_to_confirm():
    """Сторож на сам механизм: одно общее слово подтверждать не должно."""
    fragments = ["Договор с Альфа заключён на квартиру",
                 "Договор на поездку заключён с Бета"]
    assert unbound_claims("Договор на поездку заключён с Альфа", fragments) == {"Альфа"}


# ── тип сведений: определение проверяется по определяемому ────────────
#
# Прогон 445: книга говорит, что сочетание задач достигается В РАМКАХ
# ЭОТ; ответ пришёл как «ЭОТ — это сочетание задач осознания и
# изменения». Описание работы внутри метода стало определением самого
# метода. Слова все из источника, чисел нет, источник назван честно.

LINDE = ["Работа в рамках ЭОТ — это сочетание задач осознания и изменения "
         "эмоционального состояния клиента."]


def test_a_definition_of_the_whole_is_not_taken_from_a_definition_of_a_part():
    assert undefined_subjects("ЭОТ — это сочетание задач осознания и изменения",
                              LINDE) == {"ЭОТ"}


def test_the_definition_the_source_actually_gives_passes():
    assert undefined_subjects(
        "Работа в рамках ЭОТ — это сочетание задач осознания и изменения",
        LINDE) == set()


def test_a_narrower_answer_keeping_all_qualifiers_passes():
    """Ответ может добавить слов, но не отбросить их."""
    assert undefined_subjects(
        "Основная работа в рамках ЭОТ — это сочетание задач осознания",
        LINDE) == set()


def test_a_definition_absent_from_the_source_is_rejected():
    assert undefined_subjects("Перенос — это защитный механизм", LINDE) == {"Перенос"}


def test_a_mention_without_a_definitional_turn_defines_nothing():
    """«в рамках ЭОТ» без оборота не делает источник определяющим ЭОТ."""
    mention = ["Такое сочетание задач достигается в рамках ЭОТ."]
    assert undefined_subjects("ЭОТ — это сочетание задач", mention) == {"ЭОТ"}


def test_an_answer_without_a_definitional_turn_is_not_checked_as_one():
    assert undefined_subjects("В рамках ЭОТ решаются задачи осознания", LINDE) == set()


# ── доказательство у каждого утверждения, а не у ответа целиком ───────
#
# Разбор владельца 07.09.2026: ответ берёт код проекта из первого
# источника и бюджет из второго, а ссылка остаётся только на первый.
# Вес считался на весь текст сразу, длинный код перевешивал короткое
# число, и второй источник не добирал половины лучшего веса.

TWO_SOURCES = ["Проект PRJ-2026-ALPHA, ответственный Петров, срок до декабря",
               "Бюджет проекта утверждён: 450000 рублей на год"]


def test_both_sources_of_a_two_source_answer_are_cited():
    used = grounded_fragments("Проект PRJ-2026-ALPHA, бюджет 450000 рублей", TWO_SOURCES)
    assert used == (1, 2), f"источник половины ответа потерян: {used}"


def test_a_single_source_answer_still_cites_one():
    assert grounded_fragments("Проект PRJ-2026-ALPHA", TWO_SOURCES) == (1,)
    assert grounded_fragments("Бюджет 450000 рублей", TWO_SOURCES) == (2,)


def test_a_stray_word_form_still_does_not_drag_in_a_foreign_fragment():
    """Сторож прогона 388: порог веса заведён против этого и остаётся."""
    fragments = ["ссылки на мои каналы: www.b17.ru/eliah",
                 "ссылку на запись пришлю позже"]
    assert grounded_fragments("Ссылка на канал: www.b17.ru/eliah", fragments) == (1,)


# --- Соседняя строка того же семейства (прогон 494) --------------------
#
# После починки разбора таблиц в записи владельца от 23.08.2026 встали
# рядом четыре строки холестерина. На «какой у меня был холестерин»
# пришло «У вас холестерин 5.7 ммоль/л» — значение ЛПНП, поданное как
# холестерин, с честно названным источником. Общий был 8.4.

LIPID_PANEL = ("Холестерин общий: ↑ 8.4 ммоль/л (см. комментарий)\n"
               "Холестерин-ЛПНП (липопротеины низкой плотности): ↑ 5.7 ммоль/л")


def test_a_narrower_row_may_not_be_reported_under_the_broader_name():
    assert unbound_claims("У вас холестерин 5.7 ммоль/л", [LIPID_PANEL]) \
        == {"5.7 ммоль/л"}


def test_naming_the_row_as_precisely_as_the_source_binds_the_value():
    assert unbound_claims("Холестерин-ЛПНП 5.7 ммоль/л", [LIPID_PANEL]) == set()
    assert unbound_claims("Холестерин общий 8.4 ммоль/л", [LIPID_PANEL]) == set()


def test_the_broader_name_is_refused_for_the_total_row_too():
    """Правило не про «какая строка правильная», а про различимость.

    Словаря показателей здесь нет, и знать, что «холестерин» без
    уточнения означает общий, системе неоткуда. Пока в записи четыре
    строки холестерина, ответ без уточнения не подтверждается ни для
    одной из них — включая ту, которую человек и имел в виду.
    """
    assert unbound_claims("У вас холестерин 8.4 ммоль/л", [LIPID_PANEL]) \
        == {"8.4 ммоль/л"}


def test_without_a_rival_row_the_short_name_still_passes():
    """Одна строка холестерина в записи — уточнять нечего и не от чего."""
    assert unbound_claims("Уровень холестерина 8.4 ммоль/л",
                          ["Холестерин общий: ↑ 8.4 ммоль/л"]) == set()
