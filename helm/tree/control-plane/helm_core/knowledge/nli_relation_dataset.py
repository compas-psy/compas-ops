"""R4.6.F1 (владелец 03.09.2026) — детерминированный typed+directed NLI
dataset из golden fixtures, БЕЗ LLM и БЕЗ `generate_candidates()`.

R4.6.E установил: chat-generative LLM (существование/типизация одним
forced-choice или binary-запросом) даёт неприемлемый recall (0.133-0.200
на 14-case subset) даже после устранения forced-choice bias. Следующий
кандидат — purpose-built local NLI-модель (natural language inference:
premise entails/not-entails hypothesis) — компактная (0.2-0.3B),
предобученная именно на этой задаче.

Этот модуль строит ТОЛЬКО обучающий/оценочный датасет — сам NLI-scorer
здесь не вызывается (владелец п.3: «на первом этапе НЕ использовать
generate_candidates() вообще» — цель F1 измерить способность NLI
различать typed+directed relation entailment в изоляции от recall/
precision candidate-генератора, тот же принцип, что offline audit
R4.6.E шага 1: не смешивать два разных источника ошибок в одном числе).

Датасет строится ТОЛЬКО из уже существующих golden fixtures
(`semantic_benchmark_fixtures.GOLDEN_CASES`), детерминированно: на
каждый `GoldEdge` — premise (весь `case.text`, владелец: «исходный
fixture text») + hypothesis по ФИКСИРОВАННОМУ русскому шаблону на
`relation_type` (`RELATION_HYPOTHESIS_TEMPLATES`, один шаблон на
каждый тип из закрытого реестра §14.9, versioned здесь, не генерируется
моделью) — и три hard-negative варианта на то же ребро:
  - `wrong_type` — та же пара и направление, другой (заведомо неверный,
    выбранный детерминированным циклическим сдвигом по реестру) тип;
  - `reversed_direction` — тот же тип, но объекты A/B в hypothesis
    переставлены местами;
  - `false_pair` — та же relation_type/направление, но объекты — другая
    пара из ТОГО ЖЕ кейса, не образующая gold-ребро между собой
    (пропускается, если в кейсе нет такой альтернативной пары — не
    крашит построение датасета, честно недоступно, а не выдумано).

В отличие от старого (R4.6.C2-E) `long_dense_window` НЕ исключается:
исключение было про таймаут ГЕНЕРАЦИИ у 7B chat-модели на плотном
кейсе (R4.6.A), у encoder-only NLI на короткой (641 символ) premise
такого риска нет."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..models.base import SemanticRelationType
from .semantic_benchmark_fixtures import GoldenCase, GOLDEN_CASES

#: Порядок ФИКСИРОВАН (порядок объявления enum) — используется как для
#: детерминированного выбора "неверного" типа (циклический сдвиг), так
#: и как канонический перечень при проверке полноты шаблонов.
RELATION_TYPE_ORDER: tuple[str, ...] = tuple(member.value for member in SemanticRelationType)

#: Один declarative-шаблон на relation_type, `{a}`/`{b}` — подстановка
#: label/canonical_text двух объектов (a = from, b = to). Русский,
#: нейтральный, не выбирает регистр/падеж под конкретную пару —
#: NLI-модель оценивает entailment по смыслу, не по грамматической
#: идеальности.
RELATION_HYPOTHESIS_TEMPLATES: dict[str, str] = {
    "involves": "{a} участвует в {b}.",
    "has_role": "{a} имеет роль {b}.",
    "about": "{a} относится к теме {b}.",
    "located_at": "{a} находится в {b}.",
    "part_of": "{a} является частью {b}.",
    "created_by": "{a} создано {b}.",
    "owned_by": "{a} принадлежит {b}.",
    "resulted_in": "{a} привело к {b}.",
    "reason_for": "{a} — причина {b}.",
    "supports": "{a} подтверждает {b}.",
    "contradicts": "{a} противоречит {b}.",
    "supersedes": "{a} заменяет собой {b}.",
    "derived_from": "{a} основано на {b}.",
    "refers_to": "{a} ссылается на {b}.",
    "related_to": "{a} связано с {b}.",
}
assert set(RELATION_HYPOTHESIS_TEMPLATES) == set(RELATION_TYPE_ORDER), (
    "шаблон обязан существовать РОВНО на весь закрытый реестр §14.9 — ни одним "
    "типом меньше (недооценённый recall на непокрытых типах), ни одним больше "
    "(мёртвый шаблон, никогда не участвующий в датасете)")

ExampleKind = Literal["positive", "wrong_type", "reversed_direction", "false_pair"]


@dataclass(frozen=True)
class NliExample:
    case_id: str
    kind: ExampleKind
    premise: str
    hypothesis: str
    #: Ожидаемая метка entailment — True только у `kind="positive"`.
    entailed: bool
    relation_type: str
    from_ref: str
    to_ref: str


def _label_for(case: GoldenCase, ref: str) -> str:
    for e in case.entities:
        if e.ref == ref:
            return e.label
    for a in case.atoms:
        if a.ref == ref:
            return a.canonical_text
    raise KeyError(f"{case.case_id}: ref {ref!r} не найден ни среди entities, ни среди atoms")


def _wrong_type_for(relation_type: str) -> str:
    idx = RELATION_TYPE_ORDER.index(relation_type)
    return RELATION_TYPE_ORDER[(idx + 1) % len(RELATION_TYPE_ORDER)]


def _hypothesis(relation_type: str, label_a: str, label_b: str) -> str:
    return RELATION_HYPOTHESIS_TEMPLATES[relation_type].format(a=label_a, b=label_b)


def _false_pair(case: GoldenCase, from_ref: str, to_ref: str) -> tuple[str, str] | None:
    """Первая (в детерминированном порядке — отсортировано по refs) пара
    объектов ТОГО ЖЕ кейса, не образующая gold-ребро между собой и не
    совпадающая с исходной парой ни в каком порядке. `None`, если в
    кейсе нет такой альтернативы — построение датасета не крашится, эта
    hard-negative просто недоступна для этого ребра."""
    refs = sorted([e.ref for e in case.entities] + [a.ref for a in case.atoms])
    gold_pairs = {frozenset((edge.from_ref, edge.to_ref)) for edge in case.edges}
    original = frozenset((from_ref, to_ref))
    for i, r1 in enumerate(refs):
        for r2 in refs[i + 1:]:
            pair = frozenset((r1, r2))
            if pair != original and pair not in gold_pairs:
                return r1, r2
    return None


def build_examples(cases: tuple[GoldenCase, ...] = GOLDEN_CASES) -> list[NliExample]:
    """Детерминированный список примеров — один positive + до трёх
    hard-negative на каждое `GoldEdge` каждого кейса с рёбрами.
    Порядок вывода стабилен между вызовами (тот же порядок `cases`,
    тот же порядок `case.edges`, никакой случайности/множеств во
    внешнем цикле)."""
    examples: list[NliExample] = []
    for case in cases:
        for edge in case.edges:
            label_a = _label_for(case, edge.from_ref)
            label_b = _label_for(case, edge.to_ref)

            examples.append(NliExample(
                case_id=case.case_id, kind="positive",
                premise=case.text, hypothesis=_hypothesis(edge.relation_type, label_a, label_b),
                entailed=True, relation_type=edge.relation_type,
                from_ref=edge.from_ref, to_ref=edge.to_ref))

            wrong_type = _wrong_type_for(edge.relation_type)
            examples.append(NliExample(
                case_id=case.case_id, kind="wrong_type",
                premise=case.text, hypothesis=_hypothesis(wrong_type, label_a, label_b),
                entailed=False, relation_type=wrong_type,
                from_ref=edge.from_ref, to_ref=edge.to_ref))

            examples.append(NliExample(
                case_id=case.case_id, kind="reversed_direction",
                premise=case.text, hypothesis=_hypothesis(edge.relation_type, label_b, label_a),
                entailed=False, relation_type=edge.relation_type,
                from_ref=edge.to_ref, to_ref=edge.from_ref))

            false_pair = _false_pair(case, edge.from_ref, edge.to_ref)
            if false_pair is not None:
                fp_from, fp_to = false_pair
                examples.append(NliExample(
                    case_id=case.case_id, kind="false_pair",
                    premise=case.text,
                    hypothesis=_hypothesis(edge.relation_type, _label_for(case, fp_from), _label_for(case, fp_to)),
                    entailed=False, relation_type=edge.relation_type,
                    from_ref=fp_from, to_ref=fp_to))

    return examples
