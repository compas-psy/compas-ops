"""Строки таблицы восстанавливаются при разборе, а не при проверке ответа.

ДЕФЕКТ (#156, разбор владельца 07.09.2026). Извлекатель разворачивает
лабораторный бланк по колонкам: название, значение, единица и
референсный диапазон становятся четырьмя отдельными строками. Чанкинг
режет между ними, и в чанке остаётся число без имени.

Владелец сказал точно: «Проверка на выходе не восстановит отношения,
потерянные при извлечении PDF». `synthesis.unbound_claims()` умеет НЕ
подтвердить чужое значение и не умеет вернуть своё.
"""

from helm_core.knowledge.tables import restore_table_rows

#: Дословная форма живого бланка, развёрнутого по колонкам.
SPLIT_ROW = "Холестерин общий\n6.2\nммоль/л\n3.0-5.2"


def test_a_row_split_into_columns_becomes_one_line():
    assert restore_table_rows(SPLIT_ROW) == "Холестерин общий: 6.2 ммоль/л (3.0-5.2)"


def test_every_field_stays_distinguishable():
    """Название, значение, единица и диапазон обязаны остаться различимы."""
    restored = restore_table_rows(SPLIT_ROW)
    assert "Холестерин общий:" in restored
    assert "6.2" in restored
    assert "ммоль/л" in restored
    assert "(3.0-5.2)" in restored


def test_a_row_without_a_unit_keeps_its_reference_range():
    assert restore_table_rows("(RBC) Эритроциты\n5.45\n4.3-5.7") \
        == "(RBC) Эритроциты: 5.45 (4.3-5.7)"


def test_value_and_unit_on_one_line_also_bind():
    assert restore_table_rows("Холестерин общий\n6.2 ммоль/л") \
        == "Холестерин общий: 6.2 ммоль/л"


def test_a_counted_blood_unit_with_a_power_is_recognised():
    """«10^12/л» и «10*9/л» — обычная запись счётных показателей крови."""
    assert restore_table_rows("Тромбоциты\n250\n10*9/л\n180-320") \
        == "Тромбоциты: 250 10*9/л (180-320)"


def test_several_rows_in_a_row_are_all_restored():
    text = "Холестерин общий\n6.2\nммоль/л\nЭритроциты\n5.45\n10^12/л"
    assert restore_table_rows(text) == (
        "Холестерин общий: 6.2 ммоль/л\nЭритроциты: 5.45 10^12/л")


def test_an_already_whole_row_is_left_alone():
    whole = "Холестерин общий: 6.2 ммоль/л"
    assert restore_table_rows(whole) == whole


def test_prose_is_not_glued_into_a_fake_measurement():
    """Заголовок главы и следующий за ним год — не показатель и значение.

    Одного голого числа под строкой с буквами недостаточно: ячейкой она
    признаётся, либо когда рядом единица измерения, либо когда ячеек
    несколько. И то и другое бывает только в таблице.
    """
    assert restore_table_rows("Зависть\n1995") == "Зависть\n1995"
    assert restore_table_rows("Глава восьмая\n1995") == "Глава восьмая\n1995"


def test_ordinary_text_passes_through_unchanged():
    text = "Обычный текст про поездку.\nОн из двух строк.\n\nИ абзац после пустой."
    assert restore_table_rows(text) == text


def test_a_long_sentence_is_never_treated_as_a_label():
    text = "Он сказал, что придёт в понедельник утром обязательно\n5\nмм"
    assert restore_table_rows(text) == text
