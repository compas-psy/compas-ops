"""R4.7 (владелец 03.09.2026) — deterministic relation compiler.

Каждый auto-extractable тип обязан иметь тест на срабатывание (rule
доказывает edge) И sabotage-тест (rule НЕ доказывает — edge не
создаётся), плюс явный тест R7 doctor path («врач-уролог Иванов»
допускает извлечение, просто «Иванов» — нет)."""

from __future__ import annotations

from helm_core.knowledge.relation_compiler import compile_relations, derive_from_source, is_mentioned
from helm_core.knowledge.semantic_extract import ExtractedAtom, ExtractedEntity
from helm_core.models.base import AUTO_EXTRACTABLE_RELATIONS_V1, SemanticRelationType


def _entity(local_id, entity_type, label, aliases=()):
    return ExtractedEntity(local_id=local_id, entity_type=entity_type, label=label,
                           aliases=aliases, evidence_quote=label)


def _atom(local_id, kind, text, evidence=None):
    return ExtractedAtom(local_id=local_id, kind=kind, title=text[:20], text=text,
                         evidence_quote=evidence if evidence is not None else text)


def _edge_keys(edges):
    return {(e.from_local_id, e.relation_type, e.to_local_id) for e in edges}


def test_registry_has_exactly_the_eight_mandated_types():
    assert {t.value for t in AUTO_EXTRACTABLE_RELATIONS_V1} == {
        "involves", "has_role", "about", "located_at",
        "reason_for", "resulted_in", "supports", "derived_from",
    }
    assert SemanticRelationType.RELATED_TO not in AUTO_EXTRACTABLE_RELATIONS_V1


def test_compile_relations_is_deterministic_across_repeated_calls():
    """P9 (владелец 2026-09-04): «compiler deterministic replay» —
    `_run_case()` полагается на этот инвариант живьём (два вызова на тех
    же входах обязаны совпасть, иначе RuntimeError → FAIL), здесь он
    проверяется офлайн, на плотном многотипном входе (сущности + атомы
    всех auto-extractable семейств сразу), а не только на тривиальном
    одном-двух объектах."""
    entities = [
        _entity("e1", "PERSON", "Смирнов Олег"),
        _entity("e2", "PLACE", "кафе «Пушкинъ»"),
        _entity("e3", "CONCEPT", "уролог"),
        _entity("e4", "ORGANIZATION", "ООО «Ромашка»"),
    ]
    atoms = [
        _atom("a1", "event", "Встреча состоялась в кафе «Пушкинъ» с участием Смирнова Олега."),
        _atom("a2", "concept", "Уролог — врач, специализирующийся на болезнях мочеполовой системы."),
        _atom("a3", "fact", "Смирнов Олег работает в ООО «Ромашка»."),
        _atom("a4", "decision", "Из-за этого было решено перенести встречу."),
    ]
    first = compile_relations(entities, atoms, "")
    second = compile_relations(entities, atoms, "")

    def key(edges):
        return sorted((e.from_local_id, e.relation_type, e.to_local_id, e.role) for e in edges)

    assert key(first) == key(second)
    assert len(first) > 0, "тест должен реально упражнять правила, не сравнивать два пустых списка"


def test_is_mentioned_handles_russian_case_declension():
    """entity.label — нормализованный именительный падеж; evidence несёт
    исходный текст в падеже, требуемом грамматикой."""
    assert is_mentioned("состоялся приём Гавриловой Марины Сергеевны", "Гаврилова Марина Сергеевна")
    assert not is_mentioned("текст ни слова не говорит о докторе", "Гаврилова Марина Сергеевна")


class TestInvolves:
    def test_atom_evidence_mentions_person_creates_involves(self):
        atom = _atom("a1", "event", "приём Иванова")
        person = _entity("e1", "PERSON", "Иванов")
        edges = compile_relations([person], [atom])
        assert _edge_keys(edges) == {("a1", "involves", "e1")}

    def test_shared_raw_numeral_across_entity_and_atom_ids_is_unambiguous(self):
        """R4 EXIT FIX (владелец 2026-09-04): после исправления
        `_validate_nodes()` entity и atom из одного окна могут получить
        canonical id из одного и того же raw-номера ("e:1"/"a:1") —
        компилятор обязан адресовать edge однозначно на каждый endpoint,
        не путая их только потому, что raw-номер совпал."""
        atom = _atom("a:1", "event", "приём Иванова")
        person = _entity("e:1", "PERSON", "Иванов")
        edges = compile_relations([person], [atom])
        assert _edge_keys(edges) == {("a:1", "involves", "e:1")}

    def test_person_not_mentioned_in_this_atoms_evidence_creates_nothing(self):
        atom = _atom("a1", "event", "совещание отдела")
        person = _entity("e1", "PERSON", "Иванов")
        assert compile_relations([person], [atom]) == []

    def test_place_target_never_produced_by_involves(self):
        """PLACE исключён из involves намеренно (см. модуль) — даже если
        упомянуто в evidence атома, involves на него не порождается."""
        atom = _atom("a1", "event", "встреча в кафе «Пушкинъ»")
        place = _entity("e1", "PLACE", "кафе «Пушкинъ»")
        edges = compile_relations([place], [atom])
        assert not any(e.relation_type == "involves" for e in edges)

    def test_r7_doctor_path_role_doctor_from_explicit_marker(self):
        """Владелец: «врач-уролог Иванов» допускает role/specialty
        extraction — «врач-...» непосредственно перед именем."""
        atom = _atom("a1", "event", "состоялся приём врача-уролога Кириченко Сергея Александровича")
        person = _entity("e1", "PERSON", "Кириченко Сергей Александрович")
        edges = compile_relations([person], [atom])
        involves = [e for e in edges if e.relation_type == "involves"]
        assert len(involves) == 1
        assert involves[0].role == "doctor"

    def test_r7_bare_name_without_doctor_marker_gets_no_role(self):
        """Владелец: просто «Иванов» — role НЕ извлекается."""
        atom = _atom("a1", "event", "на совещании присутствовал Иванов")
        person = _entity("e1", "PERSON", "Иванов")
        edges = compile_relations([person], [atom])
        involves = [e for e in edges if e.relation_type == "involves"]
        assert len(involves) == 1
        assert involves[0].role is None


class TestHasRole:
    def test_role_marker_adjacent_to_name_creates_has_role(self):
        """R7: EVENT->INVOLVES(role=doctor)->PERSON и отдельно
        PERSON->HAS_ROLE->CONCEPT(medical_specialty), из ТОЙ ЖЕ evidence."""
        atom = _atom("a1", "event", "приём врача-нефролога Гавриловой Марины Сергеевны")
        person = _entity("e1", "PERSON", "Гаврилова Марина Сергеевна")
        specialty = _entity("e2", "CONCEPT", "нефролог")
        edges = compile_relations([person, specialty], [atom])
        assert ("e1", "has_role", "e2") in _edge_keys(edges)
        assert ("a1", "involves", "e1") in _edge_keys(edges)

    def test_bare_name_far_from_any_concept_creates_no_has_role(self):
        """Владелец: просто «Иванов» — HAS_ROLE НЕ создаётся."""
        atom = _atom("a1", "event", "на совещании присутствовал Иванов")
        person = _entity("e1", "PERSON", "Иванов")
        unrelated_concept = _entity("e2", "CONCEPT", "инфляция")
        edges = compile_relations([person, unrelated_concept], [_atom("a2", "fact", "Обсуждали инфляцию.")])
        assert not any(e.relation_type == "has_role" for e in edges)

    def test_concept_mentioned_far_away_in_same_evidence_does_not_count(self):
        """Проверка расстояния: концепт, упомянутый в том же evidence, но
        далеко от имени (сверх порога), не должен создавать has_role —
        иначе это неотличимо от «упомянуты в одном абзаце»."""
        far_text = ("Иванов " + "слово " * 20 + "нефролог")
        atom = _atom("a1", "fact", far_text)
        person = _entity("e1", "PERSON", "Иванов")
        specialty = _entity("e2", "CONCEPT", "нефролог")
        edges = compile_relations([person, specialty], [atom])
        assert not any(e.relation_type == "has_role" for e in edges)


class TestAbout:
    def test_concept_mentioned_in_atom_evidence_creates_about(self):
        atom = _atom("a1", "concept", "Уролог — врач, специализирующийся на болезнях мочеполовой системы.")
        concept = _entity("e1", "CONCEPT", "уролог")
        edges = compile_relations([concept], [atom])
        assert _edge_keys(edges) == {("a1", "about", "e1")}

    def test_fact_atom_with_explicit_topic_marker_creates_about(self):
        """Не только `kind == "concept"` доказывает тему — явный оборот
        («пример X») в fact-атоме тоже (GOLDEN_CASES `lecture_concept`:
        «привёл пример гиперинфляции» — fact, не concept)."""
        atom = _atom("a2", "fact", "Лектор Соколов привёл пример гиперинфляции как крайней формы.")
        concept = _entity("e3", "CONCEPT", "гиперинфляции")
        edges = compile_relations([concept], [atom])
        assert _edge_keys(edges) == {("a2", "about", "e3")}

    def test_bare_mention_in_fact_atom_without_topic_marker_produces_no_about(self):
        """P5 (владелец 2026-09-04, precision-first после R4 RCA
        `fact_plain`): fact-атом, просто содержащий label CONCEPT-сущности
        без единого топикального/дефиниционного оборота, не должен
        автоматически становиться ABOUT — extractor мог назвать
        CONCEPT произвольный кусок текста, это не доказывает, что атом
        действительно про эту тему."""
        atom = _atom("a1", "fact", "Рост курса доллара к концу года превысил ожидания аналитиков.")
        concept = _entity("e1", "CONCEPT", "доллара")
        assert compile_relations([concept], [atom]) == []

    def test_about_never_targets_organization_even_if_mentioned(self):
        """ORGANIZATION зарезервирована за involves — about на неё не
        порождается, иначе одна пара давала бы два типа сразу."""
        atom = _atom("a1", "fact", "Отставание вызвано поставщиком ООО «МеталлТорг».")
        org = _entity("e1", "ORGANIZATION", "ООО «МеталлТорг»")
        edges = compile_relations([org], [atom])
        assert not any(e.relation_type == "about" for e in edges)
        assert ("a1", "involves", "e1") in _edge_keys(edges)


class TestLocatedAt:
    def test_place_mentioned_in_event_evidence_creates_located_at(self):
        atom = _atom("a1", "event", "встреча состоялась в кафе «Пушкинъ»")
        place = _entity("e1", "PLACE", "кафе «Пушкинъ»")
        edges = compile_relations([place], [atom])
        assert _edge_keys(edges) == {("a1", "located_at", "e1")}

    def test_place_grounded_without_locative_marker_produces_no_edge(self):
        """P4 (владелец 2026-09-04, fail-close после R4 RCA B5): голого
        совпадения label в evidence недостаточно для LOCATED_AT — без
        предлога «в/во/на/у» непосредственно перед именем места это не
        локативный контекст, а простое упоминание (например, тема
        обсуждения), и правило обязано отказаться, а не угадать."""
        atom = _atom("a1", "fact", "Мы обсуждали Казань на совещании.")
        place = _entity("e1", "PLACE", "Казань")
        assert compile_relations([place], [atom]) == []

    def test_located_at_never_targets_organization_without_venue_marker(self):
        """Без classifier-noun (ни перед именем, ни в самом label)
        ORGANIZATION остаётся целью involves, не located_at."""
        atom = _atom("a1", "fact", "Отставание вызвано поставщиком ООО «МеталлТорг».")
        org = _entity("e1", "ORGANIZATION", "ООО «МеталлТорг»")
        edges = compile_relations([org], [atom])
        assert not any(e.relation_type == "located_at" for e in edges)

    def test_located_at_targets_organization_when_label_itself_is_a_venue_noun(self):
        """Владелец §14.7 (см. GOLDEN_CASES `purchase_warranty`, «в
        магазине «Ситилинк»»): classifier-noun считается и когда он —
        первое слово САМОГО label сущности («магазин «Комус»»), не
        только отдельным словом перед именем в evidence."""
        atom = _atom("a1", "fact", "Принтер куплен в магазине «Комус».")
        org = _entity("e1", "ORGANIZATION", "магазин «Комус»")
        edges = compile_relations([org], [atom])
        assert _edge_keys(edges) == {("a1", "located_at", "e1")}

    def test_decision_kind_atom_never_produces_located_at(self):
        atom = _atom("a1", "decision", "Решено провести встречу в кафе «Пушкинъ».")
        place = _entity("e1", "PLACE", "кафе «Пушкинъ»")
        assert compile_relations([place], [atom]) == []


class TestCausal:
    def test_reason_for_fires_only_with_explicit_cue_in_decision_evidence(self):
        fact = _atom("a1", "fact", "Тестирование проекта не завершено.")
        decision = _atom("a2", "decision", "Из-за этого было решено перенести запуск проекта на октябрь.")
        edges = compile_relations([], [fact, decision])
        assert _edge_keys(edges) == {("a1", "reason_for", "a2")}

    def test_reason_for_does_not_fire_without_cue_word(self):
        """Владелец: без явного causal evidence -> NO EDGE, даже если
        причинно-следственная связь семантически правдоподобна."""
        fact = _atom("a1", "fact", "У пациента были жалобы, типичные для гастрита.")
        decision = _atom("a2", "decision", "Терапевт поставил диагноз «гастрит».")
        assert compile_relations([], [fact, decision]) == []

    def test_resulted_in_fires_on_explicit_cue_in_source_evidence(self):
        cause = _atom("a1", "decision", "Перенос даты привело к пересмотру плана производства.")
        effect = _atom("a2", "fact", "План производства был пересмотрен.")
        edges = compile_relations([], [cause, effect])
        assert _edge_keys(edges) == {("a1", "resulted_in", "a2")}

    def test_supports_fires_on_explicit_cue_word(self):
        evidence_atom = _atom("a1", "fact", "Данные Петровой Анны подтверждают этот вывод.")
        claim = _atom("a2", "fact", "Скорость подготовки отчётов выросла.")
        edges = compile_relations([], [evidence_atom, claim])
        assert _edge_keys(edges) == {("a1", "supports", "a2")}

    def test_no_causal_edge_between_non_adjacent_atoms(self):
        """Пара НЕ соседняя в списке — правило не соединяет атомы через
        один: см. docstring _compile_causal про recall-tradeoff."""
        cause = _atom("a1", "fact", "Тестирование не завершено.")
        middle = _atom("a2", "fact", "Отдельный, не связанный факт.")
        decision = _atom("a3", "decision", "Из-за этого решено перенести запуск.")
        edges = compile_relations([], [cause, middle, decision])
        assert ("a1", "reason_for", "a3") not in _edge_keys(edges)


def test_related_to_is_never_produced_by_the_compiler():
    """Владелец: RELATED_TO запрещён как fallback. Проверяем на большом
    наборе разнородных сущностей/атомов, что ни при каких условиях
    компилятор не выдаёт related_to — его просто нет ни в одной
    `_compile_*` функции, это структурная, а не вероятностная гарантия."""
    entities = [
        _entity("e1", "PERSON", "Иванов"), _entity("e2", "CONCEPT", "инфляция"),
        _entity("e3", "PLACE", "Казань"), _entity("e4", "ORGANIZATION", "ООО «Ромашка»"),
    ]
    atoms = [_atom("a1", "event", "Иванов работает в ООО «Ромашка» в Казани, обсуждая инфляцию.")]
    edges = compile_relations(entities, atoms)
    assert all(e.relation_type != "related_to" for e in edges)


def test_derived_from_is_never_produced_by_compile_relations():
    """DERIVED_FROM — исключительно provenance-функция, не текстовое
    правило (владелец: «вообще не отдавать модели»)."""
    entities = [_entity("e1", "PERSON", "Иванов")]
    atoms = [_atom("a1", "fact", "Отчёт основан на данных Иванова.")]
    edges = compile_relations(entities, atoms)
    assert all(e.relation_type != "derived_from" for e in edges)


def test_derive_from_source_is_a_pure_structural_edge():
    edge = derive_from_source("node-1", "doc-1")
    assert edge.relation_type == "derived_from"
    assert edge.from_local_id == "node-1"
    assert edge.to_local_id == "doc-1"
