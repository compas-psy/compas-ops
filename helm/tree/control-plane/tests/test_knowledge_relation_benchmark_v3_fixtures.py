"""R4.6.F1.2 (владелец 03.09.2026) — freeze-контракт `relation_benchmark_v3_fixtures`.

Эти тесты кодируют инварианты, которые ЗАМОРОЖЕННЫЙ benchmark v3 обязан
сохранять НАВСЕГДА (владелец п.9: holdout не меняется после первого
inference). Не про качество формулировок (это ручная работа, см. сам
модуль) — про структурные гарантии, без которых числа отчёта нельзя
доверять: каждое ребро verbalizable, покрытие 15/15 держится, нет
случайно продублированной пары, split не искажён."""

from __future__ import annotations

from collections import defaultdict

from helm_core.knowledge import relation_verbalizer_v3 as v3
from helm_core.knowledge.relation_benchmark_v3_fixtures import RELATION_BENCHMARK_V3_CASES
from helm_core.models.base import SemanticRelationType

ALL_TYPES = frozenset(m.value for m in SemanticRelationType)


def _node(case, ref: str) -> v3.Node:
    for e in case.entities:
        if e.ref == ref:
            return v3.Node(category="ENTITY", ref_kind=e.entity_type, label=e.label)
    for a in case.atoms:
        if a.ref == ref:
            return v3.Node(category="ATOM", ref_kind=a.kind, label=a.canonical_text)
    raise KeyError(f"{case.case_id}: ref {ref!r} not found among entities/atoms")


def test_case_ids_are_unique():
    ids = [c.case_id for c in RELATION_BENCHMARK_V3_CASES]
    assert len(ids) == len(set(ids))


def test_split_is_only_calibration_or_final_holdout():
    assert {c.split for c in RELATION_BENCHMARK_V3_CASES} == {"calibration", "final_holdout"}


def test_every_declared_edge_positive_and_negative_is_verbalizable():
    """Владелец: hard negatives должны быть НАСТОЯЩИМИ NLI-примерами, не
    структурно недостижимыми парами — иначе для них нет hypothesis,
    которую можно измерить."""
    failures = []
    for case in RELATION_BENCHMARK_V3_CASES:
        for p in case.entailed:
            hyp = v3.verbalize(p.relation_type, _node(case, p.from_ref), _node(case, p.to_ref))
            if hyp == v3.UNSUPPORTED_FOR_NLI:
                failures.append(f"POSITIVE {case.case_id} {p.from_ref}-{p.relation_type}->{p.to_ref}")
        for n in case.not_entailed:
            hyp = v3.verbalize(n.relation_type, _node(case, n.from_ref), _node(case, n.to_ref))
            if hyp == v3.UNSUPPORTED_FOR_NLI:
                failures.append(f"NEGATIVE {case.case_id} {n.from_ref}-{n.relation_type}->{n.to_ref}")
    assert not failures, "unverbalizable edges:\n" + "\n".join(failures)


def test_no_duplicate_edge_within_a_case_and_no_positive_negative_conflict():
    for case in RELATION_BENCHMARK_V3_CASES:
        pos_keys = [(p.from_ref, p.relation_type, p.to_ref) for p in case.entailed]
        neg_keys = [(n.from_ref, n.relation_type, n.to_ref) for n in case.not_entailed]
        assert len(pos_keys) == len(set(pos_keys)), f"{case.case_id}: duplicate positive edge"
        assert len(neg_keys) == len(set(neg_keys)), f"{case.case_id}: duplicate negative edge"
        assert set(pos_keys).isdisjoint(neg_keys), (
            f"{case.case_id}: a pair declared BOTH entailed and not_entailed — "
            "contract requires each edge to be exactly one of the two"
        )


def test_every_relation_type_has_at_least_six_positives_and_three_case_ids():
    pos_by_type: dict[str, int] = defaultdict(int)
    cases_by_type: dict[str, set[str]] = defaultdict(set)
    for case in RELATION_BENCHMARK_V3_CASES:
        for p in case.entailed:
            pos_by_type[p.relation_type] += 1
            cases_by_type[p.relation_type].add(case.case_id)

    assert set(pos_by_type) == ALL_TYPES, f"missing types entirely: {ALL_TYPES - set(pos_by_type)}"
    under_covered = {rt: n for rt, n in pos_by_type.items() if n < 6}
    assert not under_covered, f"relation types with <6 positives: {under_covered}"
    under_spread = {rt: len(cs) for rt, cs in cases_by_type.items() if len(cs) < 3}
    assert not under_spread, f"relation types with <3 distinct case_id: {under_spread}"


def test_total_hard_negatives_at_least_double_total_positives():
    total_pos = sum(len(c.entailed) for c in RELATION_BENCHMARK_V3_CASES)
    total_neg = sum(len(c.not_entailed) for c in RELATION_BENCHMARK_V3_CASES)
    assert total_pos >= 90, f"total positives {total_pos} < 90"
    assert total_neg >= 2 * total_pos, f"total negatives {total_neg} < 2x positives ({total_pos})"


def test_has_role_is_kept_structurally_distinct_from_involves_role_attribute():
    """Владелец, R7: HAS_ROLE (PERSON/ORGANIZATION -> CONCEPT, атомонезависимо)
    не должен быть подменён INVOLVES(role=...) (атом -> участник, живёт на
    ребре одного события). Хотя бы один кейс обязан явно нести ОБА паттерна
    одновременно, на разных парах, чтобы отличие было измеримо, а не только
    заявлено в ontology-документе."""
    has_both = any(
        any(p.relation_type == "has_role" for p in case.entailed)
        and any(p.relation_type == "involves" and p.role is not None for p in case.entailed)
        for case in RELATION_BENCHMARK_V3_CASES
    )
    assert has_both, "no case demonstrates has_role and involves(role=...) side by side"


def test_final_holdout_covers_all_fifteen_relation_types_at_least_once():
    """Не обязательный владельцем минимум (≥6/тип — только для всего
    benchmark), но следствие дизайна: если бы holdout пропускал тип
    целиком, отчёт по нему после заморозки был бы невозможен."""
    holdout_types = {
        p.relation_type
        for case in RELATION_BENCHMARK_V3_CASES if case.split == "final_holdout"
        for p in case.entailed
    }
    assert holdout_types == ALL_TYPES, f"missing from final_holdout: {ALL_TYPES - holdout_types}"
