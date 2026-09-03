"""R4.6.F1.2 (владелец 03.09.2026) — `RelationVerbalizerV3`. Контракт:
`ATOM.label` — цитата `canonical_text` (не родовая ссылка, как в v2, и
не сама строка без изменений, как в v1), поэтому два атома ОДНОГО kind
внутри кейса дают РАЗНЫЕ, однозначные hypothesis; реестр расширен на
все 15 типов онтологии (`docs/R4.6.F1.2-RELATION-ONTOLOGY.md`); там,
где декленация произвольного `ENTITY.label` была бы нужна, используется
именительный падеж (подлежащее/предикатив после тире) или label в
кавычках после уже просклонённого нарицательного — не декленация
самого label."""

from __future__ import annotations

from helm_core.knowledge.relation_verbalizer_v3 import (
    SYMMETRIC_RELATION_TYPES, UNSUPPORTED_FOR_NLI, Node, verbalize,
)


def test_involves_swaps_grammatical_subject_and_quotes_the_atom_text():
    atom = Node(category="ATOM", ref_kind="event", label="19 августа состоялся приём.")
    person = Node(category="ENTITY", ref_kind="PERSON", label="Кириченко Сергей Александрович")

    assert verbalize("involves", atom, person) == (
        "Кириченко Сергей Александрович участвует в событии "
        "«19 августа состоялся приём.»."
    )


def test_involves_rejects_concept_target_concepts_do_not_participate():
    atom = Node(category="ATOM", ref_kind="event", label="Событие.")
    concept = Node(category="ENTITY", ref_kind="CONCEPT", label="инфляция")

    assert verbalize("involves", atom, concept) == UNSUPPORTED_FOR_NLI


def test_quoted_reference_disambiguates_two_atoms_of_the_same_kind():
    """Ядро R4.6.F1.2: `typed_relations_variety` — четыре атома kind=fact
    — были полностью недоступны в v2 (родовая ссылка неоднозначна).
    С quoted reference они дают РАЗНЫЕ строки, не требуя проверки
    количества атомов того же kind в кейсе."""
    fact_a = Node(category="ATOM", ref_kind="fact", label="Смирнов Олег отвечает за раздел «Регионы».")
    fact_b = Node(category="ATOM", ref_kind="fact", label="Скорость подготовки отчётов выросла.")

    hyp_a = verbalize("supports", fact_a, fact_b)
    hyp_b = verbalize("supports", fact_b, fact_a)

    assert hyp_a != hyp_b
    assert "Смирнов Олег отвечает за раздел «Регионы»" in hyp_a
    assert "Скорость подготовки отчётов выросла" in hyp_a


def test_has_role_needs_no_declension_of_the_arbitrary_label():
    person = Node(category="ENTITY", ref_kind="PERSON", label="Кириченко Сергей Александрович")
    specialty = Node(category="ENTITY", ref_kind="CONCEPT", label="уролог")

    assert verbalize("has_role", person, specialty) == (
        "Кириченко Сергей Александрович занимает роль «уролог»."
    )


def test_has_role_rejects_wrong_categories():
    concept = Node(category="ENTITY", ref_kind="CONCEPT", label="уролог")
    person = Node(category="ENTITY", ref_kind="PERSON", label="Иванов")
    org = Node(category="ENTITY", ref_kind="ORGANIZATION", label="ООО «Ромашка»")

    assert verbalize("has_role", concept, person) == UNSUPPORTED_FOR_NLI
    assert verbalize("has_role", person, org) == UNSUPPORTED_FOR_NLI


def test_about_uses_dative_for_place_topic_not_prepositional():
    """Найдено при написании (не живым прогоном): «место» дательный —
    «месту», не «месте» (это предложный) — «относится к» требует
    дательного."""
    fact = Node(category="ATOM", ref_kind="fact", label="Встреча прошла в переговорной.")
    place = Node(category="ENTITY", ref_kind="PLACE", label="переговорная комната офиса")

    assert verbalize("about", fact, place) == (
        "Факт «Встреча прошла в переговорной.» относится к месту "
        "переговорная комната офиса."
    )


def test_located_at_rejects_concept_target_and_decision_source():
    event = Node(category="ATOM", ref_kind="event", label="Встреча состоялась.")
    decision = Node(category="ATOM", ref_kind="decision", label="Решено перенести встречу.")
    concept = Node(category="ENTITY", ref_kind="CONCEPT", label="уролог")
    place = Node(category="ENTITY", ref_kind="PLACE", label="кафе «Пушкинъ»")

    assert verbalize("located_at", event, concept) == UNSUPPORTED_FOR_NLI
    assert verbalize("located_at", decision, place) == UNSUPPORTED_FOR_NLI
    assert verbalize("located_at", event, place) == (
        "Событие «Встреча состоялась.» произошло в кафе «Пушкинъ»."
    )


def test_about_accepts_event_source_not_only_concept_fact_decision():
    """Найдено при написании v3 fixtures (не живым прогоном): событие тоже
    может иметь тему («лекция была посвящена теме X») — исключение EVENT
    в первой редакции контракта было излишне узким."""
    lecture = Node(category="ATOM", ref_kind="event", label="На лекции рассказали о понятии стагфляции.")
    topic = Node(category="ENTITY", ref_kind="CONCEPT", label="стагфляция")

    assert verbalize("about", lecture, topic) == (
        "Событие «На лекции рассказали о понятии стагфляции.» относится к теме стагфляция."
    )


def test_located_at_place_without_own_quotes_gets_a_classifier_to_avoid_declension():
    """Найдено живым прогоном recovery-check: «Казань» (топоним без
    собственных кавычек) в предложном падеже — «Казани», не «Казань»
    — недоступно без словаря. Классификатор «месте» снимает вопрос."""
    event = Node(category="ATOM", ref_kind="event", label="Компания открыла новый филиал в Казани.")
    place = Node(category="ENTITY", ref_kind="PLACE", label="Казань")

    assert verbalize("located_at", event, place) == (
        "Событие «Компания открыла новый филиал в Казани.» произошло в месте Казань."
    )


def test_part_of_organization_to_organization_needs_no_label_declension():
    dept = Node(category="ENTITY", ref_kind="ORGANIZATION", label="Отдел разработки")
    parent = Node(category="ENTITY", ref_kind="ORGANIZATION", label="ООО «Ромашка»")

    assert verbalize("part_of", dept, parent) == (
        "Отдел разработки входит в состав организации ООО «Ромашка»."
    )


def test_created_by_and_owned_by_use_dash_predicate_to_avoid_instrumental_case():
    fact = Node(category="ATOM", ref_kind="fact", label="Отчёт по продажам подготовлен.")
    person = Node(category="ENTITY", ref_kind="PERSON", label="Смирнов Олег")

    assert verbalize("created_by", fact, person) == "Автор факта «Отчёт по продажам подготовлен.» — Смирнов Олег."
    assert verbalize("owned_by", fact, person) == "Владелец факта «Отчёт по продажам подготовлен.» — Смирнов Олег."


def test_resulted_in_uses_causal_verb_with_gender_agreement_not_v2_happened_to():
    """v2 писал «произошёл к» (temporal coincidence, не causation) —
    исправлено на «привёл/привело к» с согласованием по роду источника."""
    fact = Node(category="ATOM", ref_kind="fact", label="Попросил помощника.")
    event = Node(category="ATOM", ref_kind="event", label="Провели дополнительное обучение.")

    assert verbalize("resulted_in", fact, event) == (
        "Факт «Попросил помощника.» привёл к событию «Провели дополнительное обучение.»."
    )


def test_reason_for_target_must_be_a_decision():
    fact = Node(category="ATOM", ref_kind="fact", label="Тестирование не завершено.")
    decision = Node(category="ATOM", ref_kind="decision", label="Перенести запуск на октябрь.")
    other_fact = Node(category="ATOM", ref_kind="fact", label="Другой факт.")

    assert verbalize("reason_for", fact, decision) == (
        "Факт «Тестирование не завершено.» — причина решения «Перенести запуск на октябрь.»."
    )
    assert verbalize("reason_for", fact, other_fact) == UNSUPPORTED_FOR_NLI


def test_supports_accusative_equals_nominative_for_inanimate_kind_nouns():
    fact_a = Node(category="ATOM", ref_kind="fact", label="Данные Петровой Анны.")
    fact_b = Node(category="ATOM", ref_kind="fact", label="Скорость выросла.")

    assert verbalize("supports", fact_a, fact_b) == (
        "Факт «Данные Петровой Анны.» подтверждает факт «Скорость выросла.»."
    )


def test_contradicts_is_registered_symmetric_and_produces_a_sentence_each_direction():
    a = Node(category="ATOM", ref_kind="fact", label="Совещание перенесено на 3 февраля.")
    b = Node(category="ATOM", ref_kind="fact", label="Совещание отменено.")

    assert "contradicts" in SYMMETRIC_RELATION_TYPES
    forward = verbalize("contradicts", a, b)
    backward = verbalize("contradicts", b, a)
    assert forward != backward
    assert "Совещание перенесено" in forward and "Совещание отменено" in forward


def test_supersedes_is_directed_new_replaces_old():
    old = Node(category="ATOM", ref_kind="decision", label="Перенести запуск на октябрь.")
    new = Node(category="ATOM", ref_kind="decision", label="Перенести запуск на ноябрь.")

    assert verbalize("supersedes", new, old) == (
        "Решение «Перенести запуск на ноябрь.» заменяет собой решение «Перенести запуск на октябрь.»."
    )


def test_derived_from_uses_gender_agreement_for_the_based_on_participle():
    report = Node(category="ATOM", ref_kind="fact", label="Итоговый отчёт составлен.")
    protocol = Node(category="ATOM", ref_kind="decision", label="Протокол совещания.")

    assert verbalize("derived_from", report, protocol) == (
        "Факт «Итоговый отчёт составлен.» основан на решении «Протокол совещания.»."
    )


def test_refers_to_targets_any_atom_kind_including_event():
    letter = Node(category="ATOM", ref_kind="fact", label="Письмо направлено заказчику.")
    protocol_event = Node(category="ATOM", ref_kind="event", label="20 января состоялось совещание.")

    assert verbalize("refers_to", letter, protocol_event) == (
        "Факт «Письмо направлено заказчику.» ссылается на событие «20 января состоялось совещание.»."
    )


def test_related_to_entity_is_symmetric_and_related_to_atom_uses_instrumental():
    concept_a = Node(category="ENTITY", ref_kind="CONCEPT", label="гиперинфляция")
    concept_b = Node(category="ENTITY", ref_kind="CONCEPT", label="инфляция")
    assert "related_to" in SYMMETRIC_RELATION_TYPES
    assert verbalize("related_to", concept_a, concept_b) == "Существует связь между «гиперинфляция» и «инфляция»."

    fact_a = Node(category="ATOM", ref_kind="fact", label="Факт А.")
    fact_b = Node(category="ATOM", ref_kind="fact", label="Факт Б.")
    assert verbalize("related_to", fact_a, fact_b) == "Существует связь между фактом «Факт А.» и фактом «Факт Б.»."


def test_unregistered_combination_returns_unsupported_not_a_forced_string():
    atom = Node(category="ATOM", ref_kind="event", label="Событие.")
    person = Node(category="ENTITY", ref_kind="PERSON", label="Х")

    assert verbalize("has_role", atom, person) == UNSUPPORTED_FOR_NLI
    assert verbalize("part_of", person, atom) == UNSUPPORTED_FOR_NLI
