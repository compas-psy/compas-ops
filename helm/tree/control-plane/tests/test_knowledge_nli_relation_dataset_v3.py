"""R4.6.F1.2 — `build_examples_v3`: контракт построения датасета из
замороженного `RELATION_BENCHMARK_V3_CASES` через `RelationVerbalizerV3`.
Не про качество/грамматику формулировок (это уже проверено в
`test_knowledge_relation_verbalizer_v3.py` и
`test_knowledge_relation_benchmark_v3_fixtures.py`) — про то, что builder
детерминирован и честно прокидывает `split`/label, ничего не выводя
эвристикой (в отличие от v1)."""

from __future__ import annotations

from helm_core.knowledge.nli_relation_dataset_v3 import build_examples_v3
from helm_core.knowledge.relation_benchmark_v3_fixtures import RELATION_BENCHMARK_V3_CASES


def test_deterministic_across_calls():
    assert build_examples_v3() == build_examples_v3()


def test_total_count_matches_sum_of_entailed_and_not_entailed():
    total = sum(len(c.entailed) + len(c.not_entailed) for c in RELATION_BENCHMARK_V3_CASES)
    assert len(build_examples_v3()) == total


def test_positive_count_matches_entailed_and_negative_matches_not_entailed():
    examples = build_examples_v3()
    positives = [e for e in examples if e.entailed]
    negatives = [e for e in examples if not e.entailed]
    assert len(positives) == sum(len(c.entailed) for c in RELATION_BENCHMARK_V3_CASES)
    assert len(negatives) == sum(len(c.not_entailed) for c in RELATION_BENCHMARK_V3_CASES)


def test_split_is_inherited_from_the_case_not_recomputed():
    examples = build_examples_v3()
    split_by_case = {c.case_id: c.split for c in RELATION_BENCHMARK_V3_CASES}
    assert all(e.split == split_by_case[e.case_id] for e in examples)


def test_premise_is_the_full_case_text():
    examples = build_examples_v3()
    text_by_case = {c.case_id: c.text for c in RELATION_BENCHMARK_V3_CASES}
    assert all(e.premise == text_by_case[e.case_id] for e in examples)


def test_hypothesis_is_never_the_unsupported_sentinel():
    from helm_core.knowledge.relation_verbalizer_v3 import UNSUPPORTED_FOR_NLI
    assert all(e.hypothesis != UNSUPPORTED_FOR_NLI for e in build_examples_v3())


def test_calibration_and_final_holdout_both_present():
    examples = build_examples_v3()
    splits = {e.split for e in examples}
    assert splits == {"calibration", "final_holdout"}
