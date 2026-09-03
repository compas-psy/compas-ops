"""R4.6.F1.2 (владелец 03.09.2026) — детерминированный NLI dataset из
ЗАМОРОЖЕННОГО `relation_benchmark_v3_fixtures.RELATION_BENCHMARK_V3_CASES`,
через `RelationVerbalizerV3` (quoted reference, не родовая ссылка v2 и не
`canonical_text`-как-именная-группа v1). В отличие от v1/v2 (`build_examples`
в `nli_relation_dataset.py`) hard negatives здесь НЕ выводятся эвристикой
(`wrong_type`/`reversed_direction` циклическим сдвигом, `false_pair` на
«нет в gold = ложно») — они explicit, объявлены вручную в самих fixtures
(`RelationCaseV3.not_entailed`, каждый с `reason`), это и есть точка
R4.6.F1.1/F1.2: false_pair v1 был методологически недоказан.

`split` каждого примера наследуется от `RelationCaseV3.split` — заморожен
на уровне fixtures, здесь только прокидывается, не решается заново."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from . import relation_verbalizer_v3 as v3
from .relation_benchmark_v3_fixtures import RELATION_BENCHMARK_V3_CASES, RelationCaseV3

Split = Literal["calibration", "final_holdout"]


@dataclass(frozen=True)
class NliExampleV3:
    case_id: str
    split: Split
    premise: str
    hypothesis: str
    #: Ожидаемая метка entailment — True только для примеров, построенных
    #: из `RelationCaseV3.entailed`.
    entailed: bool
    relation_type: str
    from_ref: str
    to_ref: str


def _node(case: RelationCaseV3, ref: str) -> v3.Node:
    for e in case.entities:
        if e.ref == ref:
            return v3.Node(category="ENTITY", ref_kind=e.entity_type, label=e.label)
    for a in case.atoms:
        if a.ref == ref:
            return v3.Node(category="ATOM", ref_kind=a.kind, label=a.canonical_text)
    raise KeyError(f"{case.case_id}: ref {ref!r} не найден ни среди entities, ни среди atoms")


def build_examples_v3(cases: tuple[RelationCaseV3, ...] = RELATION_BENCHMARK_V3_CASES) -> list[NliExampleV3]:
    """Детерминированный список: один пример на каждый `entailed` +
    каждый `not_entailed` во всех кейсах, в порядке их объявления.
    Падает (`AssertionError`), если freeze-контракт нарушен и
    какая-то пара оказалась `UNSUPPORTED_FOR_NLI` — это должно быть
    исключено `test_knowledge_relation_benchmark_v3_fixtures.py` ДО
    вызова этой функции, здесь — defense in depth, не первичная проверка."""
    examples: list[NliExampleV3] = []
    for case in cases:
        for p in case.entailed:
            hyp = v3.verbalize(p.relation_type, _node(case, p.from_ref), _node(case, p.to_ref))
            assert hyp != v3.UNSUPPORTED_FOR_NLI, (
                f"{case.case_id}: positive {p.from_ref}-{p.relation_type}->{p.to_ref} "
                "unverbalizable — freeze-контракт нарушен")
            examples.append(NliExampleV3(
                case_id=case.case_id, split=case.split, premise=case.text, hypothesis=hyp,
                entailed=True, relation_type=p.relation_type, from_ref=p.from_ref, to_ref=p.to_ref))
        for n in case.not_entailed:
            hyp = v3.verbalize(n.relation_type, _node(case, n.from_ref), _node(case, n.to_ref))
            assert hyp != v3.UNSUPPORTED_FOR_NLI, (
                f"{case.case_id}: negative {n.from_ref}-{n.relation_type}->{n.to_ref} "
                "unverbalizable — freeze-контракт нарушен")
            examples.append(NliExampleV3(
                case_id=case.case_id, split=case.split, premise=case.text, hypothesis=hyp,
                entailed=False, relation_type=n.relation_type, from_ref=n.from_ref, to_ref=n.to_ref))
    return examples
