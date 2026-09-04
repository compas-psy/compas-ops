"""R4 (§14.18) — движок сопоставления/метрик проверяется НА ПОДДЕЛЬНЫХ
ответах, до единого вызова Ollama. §10 CLAUDE.md (дисциплина «докажи, что
проверка может упасть») применяется и здесь: каждый safety-счётчик ниже
сначала показан нулевым на правильном ответе, потом — ненулевым на
испорченном, иначе можно было бы никогда не заметить, что счётчик молча
всегда равен нулю."""

from __future__ import annotations

from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_benchmark_metrics import aggregate, evaluate_case
from helm_core.knowledge.semantic_extract import (
    ExtractedAtom, ExtractedEdge, ExtractedEntity, WindowExtraction,
)

_BY_ID = {c.case_id: c for c in GOLDEN_CASES}


def test_perfect_answer_scores_full_marks_on_doctor_visit():
    case = _BY_ID["doctor_visit"]
    extraction = WindowExtraction(
        entities=[ExtractedEntity(local_id="x1", entity_type="PERSON",
                                  label="Кириченко Сергей Александрович", subtype="doctor")],
        atoms=[ExtractedAtom(local_id="y1", kind="event",
                             title="Приём уролога",
                             text=("19 августа 2026 года состоялся приём врача-уролога "
                                   "Кириченко Сергея Александровича."),
                             occurred_at="2026-08-19", date_precision="day")],
        edges=[ExtractedEdge(from_local_id="y1", relation_type="involves",
                             to_local_id="x1", role="doctor")],
    )
    score = evaluate_case(case, extraction)
    assert score.entities_matched == 1 and score.entities_extracted_extra == 0
    assert score.entity_type_correct == 1
    assert score.subtype_correct == 1
    assert score.atoms_matched == 1
    assert score.atom_kind_correct == 1
    assert score.date_correct == 1
    assert score.edges_matched == 1 and score.relation_type_correct == 1
    assert score.material_hallucinations == 0


def test_related_to_gold_edge_is_not_scoreable_for_a_compiler_driven_run():
    """P6 (владелец 2026-09-04, R4 RCA `lecture_concept`: подтверждённый
    evaluator-дефект, не fixture-дефект) — `RELATED_TO` сознательно
    исключён из `AUTO_EXTRACTABLE_RELATIONS_V1`: детерминированный
    compiler структурно не может его произвести ни при каком качестве
    extraction. `lecture_concept` — реальный golden-кейс с таким
    ребром (`e3 related_to e1`) — не редактируется; вместо этого оно не
    должно входить в `edges_gold_scoreable`, иначе даже ИДЕАЛЬНОЕ
    покрытие трёх auto-extractable рёбер этого кейса не даёт полный
    recall/отсутствие «связь не найдена»."""
    case = _BY_ID["lecture_concept"]
    extraction = WindowExtraction(
        entities=[
            ExtractedEntity(local_id="c1", entity_type="CONCEPT", label="инфляция"),
            ExtractedEntity(local_id="p1", entity_type="PERSON", label="Соколов"),
            ExtractedEntity(local_id="c2", entity_type="CONCEPT", label="гиперинфляция"),
        ],
        atoms=[
            ExtractedAtom(local_id="a1", kind="concept", title="Инфляция",
                         text="Инфляция — устойчивый рост общего уровня цен."),
            ExtractedAtom(local_id="a2", kind="fact", title="Пример",
                         text="Лектор Соколов привёл пример гиперинфляции как крайней формы этого явления."),
        ],
        edges=[
            ExtractedEdge(from_local_id="a1", relation_type="about", to_local_id="c1"),
            ExtractedEdge(from_local_id="a2", relation_type="involves", to_local_id="p1"),
            ExtractedEdge(from_local_id="a2", relation_type="about", to_local_id="c2"),
            # Намеренно НЕТ ребра c2->related_to->c1 — компилятор не
            # производит related_to ни при каких обстоятельствах.
        ],
    )
    score = evaluate_case(case, extraction)
    assert score.edges_gold_scoreable == 3, "related_to не должен входить в знаменатель"
    assert score.edges_matched == 3
    assert score.edges_extracted_extra == 0
    assert not any("не найдена" in note for note in score.notes)


def test_empty_answer_is_all_misses_not_a_crash():
    case = _BY_ID["doctor_visit"]
    score = evaluate_case(case, WindowExtraction())
    assert score.entities_matched == 0
    assert score.atoms_matched == 0
    assert score.material_hallucinations == 0  # молчание — не выдумка


def test_same_label_different_entities_are_kept_apart_by_role():
    case = _BY_ID["same_label_different_entities"]
    extraction = WindowExtraction(
        entities=[
            ExtractedEntity(local_id="doc", entity_type="PERSON", label="Иванов", subtype="doctor"),
            ExtractedEntity(local_id="law", entity_type="PERSON", label="Иванов", subtype="lawyer"),
        ],
        atoms=[
            ExtractedAtom(local_id="a1", kind="event", title="Приём", text="Приём вёл терапевт Иванов."),
            ExtractedAtom(local_id="a2", kind="fact", title="Подписание",
                          text="Документы по этому визиту подписал юрист Иванов из страховой компании."),
        ],
        edges=[
            ExtractedEdge(from_local_id="a1", relation_type="involves", to_local_id="doc", role="doctor"),
            ExtractedEdge(from_local_id="a2", relation_type="involves", to_local_id="law", role="lawyer"),
        ],
    )
    score = evaluate_case(case, extraction)
    assert score.entities_matched == 2
    assert score.subtype_correct == 2, "роль в ребре должна была развести две одинаковые метки"


def test_merging_same_label_entities_into_one_shows_up_as_a_miss():
    """Ошибка модели — не должна тонуть в общем зачёте: одна сущность вместо
    двух обязана дать entities_matched=1 при entities_gold=2."""
    case = _BY_ID["same_label_different_entities"]
    extraction = WindowExtraction(
        entities=[ExtractedEntity(local_id="merged", entity_type="PERSON", label="Иванов")],
        atoms=[
            ExtractedAtom(local_id="a1", kind="event", title="Приём", text="Приём вёл терапевт Иванов."),
        ],
        edges=[ExtractedEdge(from_local_id="a1", relation_type="involves", to_local_id="merged")],
    )
    score = evaluate_case(case, extraction)
    assert score.entities_gold == 2
    assert score.entities_matched == 1


def test_fabricated_precise_date_on_unresolvable_reference_is_flagged():
    case = _BY_ID["date_unknown"]
    good = evaluate_case(case, WindowExtraction(
        atoms=[ExtractedAtom(local_id="a1", kind="event", title="Встреча",
                             text="В прошлый вторник встречались по поводу нового контракта.",
                             occurred_at=None, date_precision="unknown")]))
    assert good.fabricated_dates == 0

    bad = evaluate_case(case, WindowExtraction(
        atoms=[ExtractedAtom(local_id="a1", kind="event", title="Встреча",
                             text="В прошлый вторник встречались по поводу нового контракта.",
                             occurred_at="2026-08-25", date_precision="day")]))
    assert bad.fabricated_dates == 1
    assert bad.material_hallucinations >= 1


def test_forbidden_relation_between_unrelated_entities_is_flagged():
    case = _BY_ID["provocative_no_relation_invention"]
    extraction_ok = WindowExtraction(
        entities=[
            ExtractedEntity(local_id="e1", entity_type="PERSON", label="Кузнецов Игорь"),
            ExtractedEntity(local_id="e2", entity_type="PERSON", label="Волкова Елена"),
        ],
        atoms=[
            ExtractedAtom(local_id="a1", kind="fact", title="Кузнецов",
                          text="Кузнецов Игорь работает в отделе продаж."),
            ExtractedAtom(local_id="a2", kind="fact", title="Волкова",
                          text="Волкова Елена работает в отделе кадров."),
        ],
        edges=[
            ExtractedEdge(from_local_id="a1", relation_type="involves", to_local_id="e1"),
            ExtractedEdge(from_local_id="a2", relation_type="involves", to_local_id="e2"),
        ],
    )
    ok_score = evaluate_case(case, extraction_ok)
    assert ok_score.fabricated_relations == 0

    extraction_bad = WindowExtraction(
        entities=extraction_ok.entities, atoms=extraction_ok.atoms,
        edges=[*extraction_ok.edges,
              ExtractedEdge(from_local_id="e1", relation_type="related_to", to_local_id="e2")],
    )
    bad_score = evaluate_case(case, extraction_bad)
    assert bad_score.fabricated_relations == 1


def test_no_knowledge_violation_when_model_invents_something():
    case = _BY_ID["no_knowledge"]
    assert evaluate_case(case, WindowExtraction()).no_knowledge_violation is False

    invented = WindowExtraction(
        entities=[ExtractedEntity(local_id="x", entity_type="PERSON", label="Кто-то")])
    assert evaluate_case(case, invented).no_knowledge_violation is True


def test_unsupported_fact_on_entity_without_facts_is_flagged():
    case = _BY_ID["provocative_no_fact_invention"]
    clean = WindowExtraction(
        entities=[ExtractedEntity(local_id="e1", entity_type="PERSON", label="Соколов Артём")])
    assert evaluate_case(case, clean).unsupported_fact_additions == 0

    invented = WindowExtraction(
        entities=[ExtractedEntity(local_id="e1", entity_type="PERSON", label="Соколов Артём")],
        atoms=[ExtractedAtom(local_id="a1", kind="fact", title="Роль",
                             text="Соколов Артём отвечал за логистику.")],
        edges=[ExtractedEdge(from_local_id="a1", relation_type="involves", to_local_id="e1")],
    )
    assert evaluate_case(case, invented).unsupported_fact_additions == 1


def test_provocative_no_fact_allows_the_one_gold_fact_but_forbids_more():
    """Владелец 03.09.2026: «значится в списке участников» — тоже факт, его
    можно извлечь; запрещена именно ВЫДУМАННАЯ роль/действие сверх этого.
    Старая проверка требовала строго 0 атомов у сущности и штрафовала бы
    модель за факт, который сам gold теперь разрешает — этот тест ловит
    именно такой регресс (в отличие от теста выше, где gold-фактов вообще
    не бывает, здесь он есть, и его извлечение обязано остаться бесплатным)."""
    case = _BY_ID["provocative_no_fact_invention"]

    correct = WindowExtraction(
        entities=[ExtractedEntity(local_id="e1", entity_type="PERSON", label="Соколов Артём")],
        atoms=[ExtractedAtom(local_id="a1", kind="fact", title="Участник",
                             text="В списке участников значится Соколов Артём.")],
        edges=[ExtractedEdge(from_local_id="a1", relation_type="involves", to_local_id="e1")],
    )
    correct_score = evaluate_case(case, correct)
    assert correct_score.atoms_matched == 1
    assert correct_score.unsupported_fact_additions == 0

    invented_in_addition = WindowExtraction(
        entities=correct.entities,
        atoms=[*correct.atoms,
              ExtractedAtom(local_id="a2", kind="fact", title="Роль",
                            text="Соколов Артём отвечал за логистику.")],
        edges=[*correct.edges,
              ExtractedEdge(from_local_id="a2", relation_type="involves", to_local_id="e1")],
    )
    assert evaluate_case(case, invented_in_addition).unsupported_fact_additions == 1


def test_lost_negation_is_flagged_as_material_hallucination():
    case = _BY_ID["negative_statement"]
    faithful = WindowExtraction(atoms=[
        ExtractedAtom(local_id="a1", kind="fact", title="Диагноз",
                      text="Онкологический диагноз не подтверждён по результатам биопсии."),
        ExtractedAtom(local_id="a2", kind="fact", title="Наблюдение",
                      text="Дальнейшее наблюдение не требуется."),
    ])
    assert evaluate_case(case, faithful).inverted_negations == 0

    inverted = WindowExtraction(atoms=[
        ExtractedAtom(local_id="a1", kind="fact", title="Диагноз",
                      text="Диагностирована онкология по результатам биопсии."),
        ExtractedAtom(local_id="a2", kind="fact", title="Наблюдение", text="Наблюдение требуется."),
    ])
    score = evaluate_case(case, inverted)
    assert score.inverted_negations == 2
    assert score.material_hallucinations >= 2


def test_wrong_relation_type_on_correct_endpoints_is_not_a_typed_match():
    """Владелец 03.09.2026 (R4.6.B.1): §14.18 «relation precision on
    labeled edges» — typed идентичность `(from, type, to)`, не только
    пара конечных точек. Верная пара с неверным типом обязана СНИЗИТЬ
    normative `relation_precision`, а не остаться в нём почти-верной
    (это тот самый drift, из-за которого run 210/217 показал
    endpoint-only 0.304 вместо настоящего typed-precision 0.0)."""
    case = _BY_ID["doctor_visit"]
    right_type = WindowExtraction(
        entities=[ExtractedEntity(local_id="x1", entity_type="PERSON",
                                  label="Кириченко Сергей Александрович")],
        atoms=[ExtractedAtom(local_id="y1", kind="event", title="Приём",
                             text=("19 августа 2026 года состоялся приём врача-уролога "
                                   "Кириченко Сергея Александровича."))],
        edges=[ExtractedEdge(from_local_id="y1", relation_type="involves", to_local_id="x1")],
    )
    right_agg = aggregate([evaluate_case(case, right_type)])
    assert right_agg.relation_precision == 1.0
    assert right_agg.endpoint_relation_precision == 1.0

    wrong_type = WindowExtraction(
        entities=right_type.entities, atoms=right_type.atoms,
        edges=[ExtractedEdge(from_local_id="y1", relation_type="contradicts", to_local_id="x1")],
    )
    wrong_score = evaluate_case(case, wrong_type)
    assert wrong_score.edges_matched == 1, "конечные точки всё ещё найдены верно"
    assert wrong_score.relation_type_correct == 0, "но тип — нет"

    wrong_agg = aggregate([wrong_score])
    assert wrong_agg.relation_precision == 0.0, (
        "typed relation_precision обязан УПАСТЬ при неверном типе на верной паре")
    assert wrong_agg.endpoint_relation_precision == 1.0, (
        "старая endpoint-only метрика остаётся диагностическим числом — "
        "она НЕ портится, именно поэтому normative-гейт больше не читает её напрямую")


def test_missing_relation_type_registry_still_lowers_typed_precision():
    """`related_to` (нормализованный владельцем 03.09.2026 fallback вне
    закрытого реестра §14.9, R4.6.B) — валидный ЗНАЧЕНИЕ типа, но не то,
    что ждёт gold `involves`: typed-идентичность обязана его отличать
    так же, как явно неверный тип выше."""
    case = _BY_ID["doctor_visit"]
    entities = [ExtractedEntity(local_id="x1", entity_type="PERSON",
                                label="Кириченко Сергей Александрович")]
    atoms = [ExtractedAtom(local_id="y1", kind="event", title="Приём",
                           text=("19 августа 2026 года состоялся приём врача-уролога "
                                 "Кириченко Сергея Александровича."))]
    extraction = WindowExtraction(
        entities=entities, atoms=atoms,
        edges=[ExtractedEdge(from_local_id="y1", relation_type="related_to", to_local_id="x1")])

    agg = aggregate([evaluate_case(case, extraction)])
    assert agg.relation_precision == 0.0
    assert agg.endpoint_relation_precision == 1.0


def test_aggregate_reduces_over_multiple_cases_without_crashing():
    scores = [evaluate_case(case, WindowExtraction()) for case in GOLDEN_CASES]
    agg = aggregate(scores)
    assert agg.cases_scored == len(GOLDEN_CASES)
    assert 0.0 <= agg.entity_recall <= 1.0
    assert agg.no_knowledge_violations == 0
    # per_category: каждая категория из фикстур должна быть представлена.
    all_categories = {cat for case in GOLDEN_CASES for cat in case.categories}
    assert set(agg.per_category) == all_categories
