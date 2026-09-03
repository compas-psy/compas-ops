"""R4.6.F1.1 (владелец 03.09.2026) — RelationVerbalizerV2. Проверяется
контракт: ENTITY подставляется своим label, ATOM — родовой именной
группой (никогда не `canonical_text` целиком), направление `involves`
соответствует истинной семантике §14.9 (участник — субъект, атом —
обстоятельство), нет verbalizer'а — `UNSUPPORTED_FOR_NLI`, не
притянутая строка, `related_to`/`contradicts` зафиксированы как
симметричные."""

from __future__ import annotations

from helm_core.knowledge.relation_verbalizer_v2 import (
    SYMMETRIC_RELATION_TYPES, UNSUPPORTED_FOR_NLI, Node, verbalize,
)


def test_involves_atom_to_person_swaps_grammatical_subject_to_the_participant():
    """Владелец: «EVENT/FACT -> PERSON, INVOLVES: "<PERSON> участвует в
    описанном событии."» — направление НЕ naive from=subject: истинная
    семантика involves — атом вовлекает участника, участник грамматически
    субъект «участвует»."""
    atom = Node(category="ATOM", ref_kind="event")
    person = Node(category="ENTITY", ref_kind="PERSON", label="Кириченко Сергей Александрович")

    hypothesis = verbalize("involves", atom, person)

    assert hypothesis == "Кириченко Сергей Александрович участвует в описанном событии."


def test_atom_referent_is_never_the_raw_canonical_text():
    """Ядро дефекта v1: canonical_text (целое предложение) никогда не
    должен попадать в hypothesis как именная группа — только родовая
    группа по kind."""
    atom = Node(category="ATOM", ref_kind="fact", label="Иванова Мария работает менеджером по продажам.")
    entity = Node(category="ENTITY", ref_kind="ORGANIZATION", label="ООО «Ромашка»")

    hypothesis = verbalize("involves", atom, entity)

    assert "Иванова Мария работает менеджером" not in hypothesis
    assert "описанном факте" in hypothesis


def test_located_at_keeps_atom_as_grammatical_subject_with_correct_gender_agreement():
    event = Node(category="ATOM", ref_kind="event")
    place = Node(category="ENTITY", ref_kind="PLACE", label="кафе «Пушкинъ»")
    fact = Node(category="ATOM", ref_kind="fact")

    assert verbalize("located_at", event, place) == "Описанное событие произошло в кафе «Пушкинъ»."
    # "факт" — мужской род, "произошёл", не "произошло" (проверка
    # согласования по роду, не общая заглушка).
    assert verbalize("located_at", fact, place) == "Описанный факт произошёл в кафе «Пушкинъ»."


def test_about_uses_topic_wording_for_concept_and_organization_wording_for_org():
    concept_atom = Node(category="ATOM", ref_kind="concept")
    concept_entity = Node(category="ENTITY", ref_kind="CONCEPT", label="уролог")
    fact_atom = Node(category="ATOM", ref_kind="fact")
    org_entity = Node(category="ENTITY", ref_kind="ORGANIZATION", label="ООО «МеталлТорг»")

    assert "теме уролог" in verbalize("about", concept_atom, concept_entity)
    assert "организации ООО «МеталлТорг»" in verbalize("about", fact_atom, org_entity)


def test_reason_for_uses_genitive_case_for_the_target_atom():
    fact = Node(category="ATOM", ref_kind="fact")
    decision = Node(category="ATOM", ref_kind="decision")

    assert verbalize("reason_for", fact, decision) == "Описанный факт — причина описанного решения."


def test_resulted_in_dative_case_and_gender_agreement():
    fact = Node(category="ATOM", ref_kind="fact")
    event = Node(category="ATOM", ref_kind="event")

    assert verbalize("resulted_in", fact, event) == "Описанный факт произошёл к описанному событию."


def test_supports_uses_accusative_which_equals_nominative_for_inanimate_nouns():
    fact_a = Node(category="ATOM", ref_kind="fact")
    fact_b = Node(category="ATOM", ref_kind="fact")

    assert verbalize("supports", fact_a, fact_b) == "Описанный факт подтверждает описанный факт."


def test_related_to_is_symmetric_construction_with_no_gender_agreement_needed():
    a = Node(category="ENTITY", ref_kind="CONCEPT", label="гиперинфляция")
    b = Node(category="ENTITY", ref_kind="CONCEPT", label="инфляция")

    forward = verbalize("related_to", a, b)
    backward = verbalize("related_to", b, a)

    assert forward == "Существует связь между «гиперинфляция» и «инфляция»."
    # Симметричная конструкция — переставленные аргументы дают ДРУГУЮ
    # строку (порядок в тексте меняется), но описывают ОДНО И ТО ЖЕ
    # истинностное значение — это и проверяет SYMMETRIC_RELATION_TYPES,
    # не текстовое совпадение.
    assert backward == "Существует связь между «инфляция» и «гиперинфляция»."


def test_related_to_and_contradicts_are_registered_as_symmetric():
    assert SYMMETRIC_RELATION_TYPES == frozenset({"related_to", "contradicts"})


def test_located_at_rejects_concept_targets_concepts_are_not_places():
    """Найдено живым прогоном offline audit: без этой проверки
    `located_at` к CONCEPT давал бессмысленное «Описанное понятие
    произошло в уролог.» — CONCEPT никогда не бывает местом."""
    event = Node(category="ATOM", ref_kind="event")
    concept = Node(category="ENTITY", ref_kind="CONCEPT", label="уролог")

    assert verbalize("located_at", event, concept) == UNSUPPORTED_FOR_NLI


def test_unregistered_combination_returns_unsupported_not_a_forced_string():
    """Владелец п.4: has_role/part_of/created_by/... никогда не
    встречаются с валидной node-kind комбинацией в golden fixtures —
    не форсируем строку ради покрытия enum."""
    atom = Node(category="ATOM", ref_kind="event")
    person = Node(category="ENTITY", ref_kind="PERSON", label="Х")

    assert verbalize("has_role", atom, person) == UNSUPPORTED_FOR_NLI
    assert verbalize("part_of", atom, person) == UNSUPPORTED_FOR_NLI
    assert verbalize("involves", person, atom) == UNSUPPORTED_FOR_NLI  # обратная категория не регистрировалась
