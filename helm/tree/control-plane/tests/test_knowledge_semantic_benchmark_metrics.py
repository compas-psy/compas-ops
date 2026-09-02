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


def test_aggregate_reduces_over_multiple_cases_without_crashing():
    scores = [evaluate_case(case, WindowExtraction()) for case in GOLDEN_CASES]
    agg = aggregate(scores)
    assert agg.cases_scored == len(GOLDEN_CASES)
    assert 0.0 <= agg.entity_recall <= 1.0
    assert agg.no_knowledge_violations == 0
    # per_category: каждая категория из фикстур должна быть представлена.
    all_categories = {cat for case in GOLDEN_CASES for cat in case.categories}
    assert set(agg.per_category) == all_categories
