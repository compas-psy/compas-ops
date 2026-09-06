"""Контекстные якоря даты: роль определяется подписью, а не соседством.

Первая ступень конструкции, заданной владельцем 06.09.2026 взамен
`document_date → occurred_at`. Проверяется ровно то, от чего зависит
безопасность будущего наследования: дата документа, дата рождения и
дата приёма обязаны различаться, а нераспознанная подпись обязана
давать `unlabelled`, а не молча сойти за событие.

Тексты синтетические и по форме повторяют медицинский бланк; настоящих
данных здесь нет и не нужно.
"""

from __future__ import annotations

from helm_core.knowledge.temporal import (
    ROLE_DOCUMENT, ROLE_EVENT, ROLE_PLANNED, ROLE_REFERENCE, ROLE_UNLABELLED,
    find_date_anchors, inheritable_anchor,
)


def _roles(text: str) -> list[tuple[str, str]]:
    return [(anchor.value, anchor.role) for anchor in find_date_anchors(text)]


# ── формы записи ─────────────────────────────────────────────────────

def test_three_written_forms_are_all_found():
    assert _roles("Приём от 12.03.2025") == [("2025-03-12", ROLE_EVENT)]
    assert _roles("Дата исследования: 2025-03-12") == [("2025-03-12", ROLE_EVENT)]
    assert _roles("Осмотр от 12 марта 2025 года") == [("2025-03-12", ROLE_EVENT)]


def test_month_precision_is_not_promoted_to_day():
    anchors = find_date_anchors("Заключение от август 2025")
    assert [(a.value, a.precision) for a in anchors] == [("2025-08", "month")]


def test_longer_match_wins_over_the_shorter_inside_it():
    """«26 августа 2026» не должно распасться на «август 2026»: иначе у
    одной даты появятся два якоря разной точности."""
    anchors = find_date_anchors("Дата приёма: 26 августа 2026")
    assert [(a.value, a.precision) for a in anchors] == [("2026-08-26", "day")]


def test_bare_year_is_not_an_anchor():
    """В бланке «2024» чаще номер или граница нормы, чем дата."""
    assert find_date_anchors("Референс 2024 - 4096") == []


def test_impossible_dates_are_rejected():
    assert find_date_anchors("Код 45.99.2025") == []
    assert find_date_anchors("Проба 12.13.2025") == []


# ── роли ─────────────────────────────────────────────────────────────

def test_document_date_is_not_an_event():
    assert _roles("Дата выдачи: 20.03.2025") == [("2025-03-20", ROLE_DOCUMENT)]
    assert _roles("Сформирован 20.03.2025") == [("2025-03-20", ROLE_DOCUMENT)]


def test_birth_date_is_reference_not_event():
    """Самая дорогая ошибка: дата рождения, унаследованная как дата
    приёма, отправила бы каждый факт пациента в год его рождения."""
    assert _roles("Дата рождения: 04.07.1985") == [("1985-07-04", ROLE_REFERENCE)]
    assert _roles("д.р. 04.07.1985") == [("1985-07-04", ROLE_REFERENCE)]


def test_unlabelled_date_stays_unlabelled():
    assert _roles("В отделении 12.03.2025 проводился ремонт") == [
        ("2025-03-12", ROLE_UNLABELLED)]


def test_relative_marker_next_to_the_date_blocks_the_label():
    """«в прошлом году, 12.03.2025» описывает сдвиг от даты, а не саму
    дату приёма — подпись здесь доверия не заслуживает."""
    assert _roles("Приём в прошлом году, 12.03.2025") == [
        ("2025-03-12", ROLE_UNLABELLED)]


def test_label_further_than_the_window_does_not_reach():
    far = "Дата приёма" + " " * 60 + "12.03.2025"
    assert _roles(far) == [("2025-03-12", ROLE_UNLABELLED)]


# ── что можно наследовать ────────────────────────────────────────────

def test_single_event_anchor_is_inheritable():
    anchors = find_date_anchors(
        "Дата выдачи: 20.03.2025. Дата приёма: 12.03.2025. Дата рождения: 04.07.1985")
    anchor = inheritable_anchor(anchors)
    assert anchor is not None and anchor.value == "2025-03-12"


def test_two_event_anchors_are_ambiguous_and_give_nothing():
    """Два приёма в одном окне — неизвестно, к какому относится факт."""
    anchors = find_date_anchors("Приём от 12.03.2025. Повторный приём от 19.03.2025")
    assert inheritable_anchor(anchors) is None


def test_document_date_alone_is_not_inheritable():
    """Ровно тот случай, который владелец запретил: в окне одна дата, и
    это дата печати бланка."""
    anchors = find_date_anchors("Дата выдачи: 20.03.2025")
    assert inheritable_anchor(anchors) is None


def test_no_dates_at_all():
    assert find_date_anchors("Гемоглобин в пределах нормы") == []
    assert inheritable_anchor([]) is None


# ── спаны ────────────────────────────────────────────────────────────

def test_span_points_at_the_date_itself():
    text = "Дата приёма: 12.03.2025, врач принял пациента"
    anchor = find_date_anchors(text)[0]
    assert text[anchor.char_start:anchor.char_end] == "12.03.2025"
    assert anchor.quote == "12.03.2025"


def test_anchors_come_in_text_order():
    anchors = find_date_anchors("Дата приёма: 12.03.2025. Дата выдачи: 20.03.2025")
    assert [a.char_start for a in anchors] == sorted(a.char_start for a in anchors)


# ── формы, снятые с корпуса (прогон 356) ─────────────────────────────
#
# Первый словарь узнал 87 якорей из 273: я перечислил формы по своему
# представлению о бланке. Ниже — то, чем корпус подписывает даты на
# самом деле, с числом якорей на каждую группу.

def test_bare_data_in_the_form_header_is_a_document_date():
    """79 якорей, 42% всех неузнанных: «Направление №123 Дата <дата>».

    Соблазн засчитать её событием велик — в направлении это обычно и
    есть день визита. Но это ровно та подстановка, которую владелец
    запретил, и правило наследования от неё работать не должно.
    """
    assert _roles("Направление № 123 Дата 12.03.2025") == [
        ("2025-03-12", ROLE_DOCUMENT)]
    assert _roles("ЭМК № 45 Дата: 12.03.2025") == [("2025-03-12", ROLE_DOCUMENT)]
    assert inheritable_anchor(find_date_anchors("Направление № 123 Дата 12.03.2025")) is None


def test_appointment_scheduled_for_a_future_date_is_not_an_event():
    """24 якоря: «Повторная явка на 12.03.2025». Дата В БУДУЩЕМ.

    Разделяет их предлог: «от» — то, что произошло, «на» — то, что
    назначено. Унаследованная как дата приёма, эта дата поставила бы
    факту время, когда его ещё не было.
    """
    assert _roles("Повторная явка на 12.03.2025") == [("2025-03-12", ROLE_PLANNED)]
    assert _roles("Записан на 12.03.2025") == [("2025-03-12", ROLE_PLANNED)]
    assert inheritable_anchor(find_date_anchors("Повторная явка на 12.03.2025")) is None


def test_the_same_word_with_ot_is_the_past_and_is_an_event():
    """Тот же корень, другой предлог — другая роль."""
    assert _roles("Приём от 12.03.2025") == [("2025-03-12", ROLE_EVENT)]
    assert _roles("Осмотр от 12.03.2025") == [("2025-03-12", ROLE_EVENT)]


def test_modality_abbreviations_are_events():
    """«норме ЭГДС от», «УЗИ ОБП от» — прежний список знал только слово
    «исследование» и эти формы пропускал."""
    assert _roles("В норме ЭГДС от 12.03.2025") == [("2025-03-12", ROLE_EVENT)]
    assert _roles("УЗИ ОБП от 12.03.2025") == [("2025-03-12", ROLE_EVENT)]
    assert _roles("МРТ головного мозга от 12.03.2025") == [("2025-03-12", ROLE_EVENT)]


def test_regulation_reference_is_not_an_event():
    """7 якорей: «Приказ Минздрава России №123н от 12.03.2025».

    Форма «… от <дата>» здесь та же, что у ЭГДС, и без отдельного
    правила норматив попал бы в события вместе с ней.
    """
    assert _roles("Приказ Минздрава России № 123н от 12.03.2025") == [
        ("2025-03-12", ROLE_REFERENCE)]
    assert inheritable_anchor(
        find_date_anchors("Приказ Минздрава России № 123н от 12.03.2025")) is None


def test_section_header_and_material_are_events():
    """«Биохимические исследования <дата>» (6), «биопсийного
    операционного материала <дата>» (4) — дата выполнения и дата
    забора."""
    assert _roles("Биохимические исследования 12.03.2025") == [
        ("2025-03-12", ROLE_EVENT)]
    assert _roles("Дата поступления биопсийного операционного материала 12.03.2025") == [
        ("2025-03-12", ROLE_EVENT)]
