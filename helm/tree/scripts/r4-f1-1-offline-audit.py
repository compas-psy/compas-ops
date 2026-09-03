#!/usr/bin/env python3
"""R4.6.F1.1 (владелец 03.09.2026) — OFFLINE audit F1 dataset v1 vs v2.

Ни одного вызова NLI/Ollama — чистая проверка данных и логики
verbalizer'а. Печатает:
  1. Полный audit всех 37 positive-примеров v1 (case_id, relation_type,
     source/target kind, premise, v1 hypothesis, v2 hypothesis или
     UNSUPPORTED_FOR_NLI, valid=yes/no, reason).
  2. Audit hard negatives (wrong_type/reversed_direction/false_pair) —
     правила владельца: reversed_direction невалиден для симметричных
     relation_type (related_to, contradicts); false_pair ТОЛЬКО из
     явных `ForbiddenEdge` (отсутствие в case.edges НЕ доказательство
     ложности); wrong_type валиден, только если действительно
     противоречит premise (документированное ручное решение по
     единственному спорному случаю — reason_for/supports в
     decision_rationale, см. QUESTIONABLE_WRONG_TYPE ниже).
  3. Итоговую статистику: v1 invalid/questionable/unproven по
     категориям, v2 usable positives/hard negatives, покрытие по
     relation_type и по node-kind паре.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/home/user/compas-ops/helm/tree/control-plane")

from helm_core.knowledge.nli_relation_dataset import build_examples as build_examples_v1
from helm_core.knowledge.relation_verbalizer_v2 import (
    SYMMETRIC_RELATION_TYPES, UNSUPPORTED_FOR_NLI, Node, verbalize,
)
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES

#: Единственный найденный спорный случай ручного (не автоматизируемого)
#: анализа: decision_rationale a1->a2 (reason_for), cyclic wrong_type =
#: supports. "Тестирование не завершено" одновременно (а) ПРИЧИНА
#: решения перенести срок (reason_for) И правдоподобно (б) ПОДТВЕРЖДАЕТ
#: обоснованность этого решения (supports) — эти два прочтения не
#: взаимоисключающие для этой конкретной пары, значит supports здесь НЕ
#: гарантированно ложно. Помечено вручную, не автоматическим правилом —
#: следующий похожий случай потребует такого же ручного разбора, не
#: обобщения этого исключения.
QUESTIONABLE_WRONG_TYPE = {("decision_rationale", "a1", "a2")}


def node_for(case, ref: str) -> Node:
    for e in case.entities:
        if e.ref == ref:
            return Node(category="ENTITY", ref_kind=e.entity_type, label=e.label)
    for a in case.atoms:
        if a.ref == ref:
            return Node(category="ATOM", ref_kind=a.kind, label=a.canonical_text)
    raise KeyError(f"{case.case_id}: {ref!r} не найден")


def atom_kind_counts(case) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in case.atoms:
        counts[a.kind] = counts.get(a.kind, 0) + 1
    return counts


def referent_unambiguous(node: Node, counts: dict[str, int]) -> bool:
    if node.category == "ENTITY":
        return True
    return counts.get(node.ref_kind, 0) <= 1


CASES_WITH_EDGES = [c for c in GOLDEN_CASES if c.edges]
V1_EXAMPLES = build_examples_v1()


def v1_hypothesis_for(case_id: str, kind: str, from_ref: str, to_ref: str) -> str | None:
    for ex in V1_EXAMPLES:
        if ex.case_id == case_id and ex.kind == kind and ex.from_ref == from_ref and ex.to_ref == to_ref:
            return ex.hypothesis
    return None


# ============================== 1. POSITIVES ==============================
print("=" * 100)
print("AUDIT 1: 37 POSITIVE EXAMPLES (v1 vs v2)")
print("=" * 100)

pos_v1_invalid = 0
pos_v2_usable = 0
coverage_relation_type: dict[str, int] = {}
coverage_kind_pair: dict[tuple[str, str], int] = {}

for case in CASES_WITH_EDGES:
    counts = atom_kind_counts(case)
    for edge in case.edges:
        source = node_for(case, edge.from_ref)
        target = node_for(case, edge.to_ref)
        v1_hyp = v1_hypothesis_for(case.case_id, "positive", edge.from_ref, edge.to_ref)

        source_unambig = referent_unambiguous(source, counts)
        target_unambig = referent_unambiguous(target, counts)
        if not (source_unambig and target_unambig):
            v2_hyp = UNSUPPORTED_FOR_NLI
            valid = False
            reason = "AMBIGUOUS atom referent (multiple atoms of same kind in this case) — generic reference would not disambiguate"
        else:
            v2_hyp = verbalize(edge.relation_type, source, target)
            valid = v2_hyp != UNSUPPORTED_FOR_NLI
            reason = "OK" if valid else "no validated (relation_type, source_category, target_category) verbalizer registered"

        # v1 grammatical validity: любой ATOM-референт (canonical_text —
        # целое предложение) в позиции {a}/{b} — грамматически невалиден
        # как именная группа; для involves — ДОПОЛНИТЕЛЬНО обратное
        # направление (см. relation_verbalizer_v2 docstring).
        v1_invalid_reason = []
        if source.category == "ATOM":
            v1_invalid_reason.append("source is full sentence (canonical_text) used as noun phrase")
        if target.category == "ATOM":
            v1_invalid_reason.append("target is full sentence (canonical_text) used as noun phrase")
        if edge.relation_type == "involves":
            v1_invalid_reason.append("direction inverted: 'involves' means atom engages participant, "
                                     "v1 template said participant engages atom")
        v1_valid = not v1_invalid_reason
        if not v1_valid:
            pos_v1_invalid += 1
        if valid:
            pos_v2_usable += 1
            coverage_relation_type[edge.relation_type] = coverage_relation_type.get(edge.relation_type, 0) + 1
            coverage_kind_pair[(source.ref_kind, target.ref_kind)] = \
                coverage_kind_pair.get((source.ref_kind, target.ref_kind), 0) + 1

        print(f"\ncase_id={case.case_id} relation_type={edge.relation_type} "
              f"{edge.from_ref}({source.category}:{source.ref_kind}) -> {edge.to_ref}({target.category}:{target.ref_kind})")
        print(f"  premise: {case.text!r}")
        print(f"  v1 hypothesis: {v1_hyp!r}")
        print(f"  v1 valid: {'yes' if v1_valid else 'no'}" + (f" — {'; '.join(v1_invalid_reason)}" if v1_invalid_reason else ""))
        print(f"  v2 hypothesis: {v2_hyp!r}")
        print(f"  v2 valid: {'yes' if valid else 'no'} — {reason}")

print(f"\n---- SUMMARY: positives ----")
print(f"v1 total=37 invalid={pos_v1_invalid} ({pos_v1_invalid}/37)")
print(f"v2 usable={pos_v2_usable} ({pos_v2_usable}/37)")
print(f"v2 coverage by relation_type: {coverage_relation_type}")
print(f"v2 coverage by (source_kind, target_kind): {coverage_kind_pair}")
all_relation_types = {"involves", "has_role", "about", "located_at", "part_of", "created_by", "owned_by",
                      "resulted_in", "reason_for", "supports", "contradicts", "supersedes", "derived_from",
                      "refers_to", "related_to"}
uncovered = sorted(all_relation_types - set(coverage_relation_type))
print(f"relation_types with ZERO v2 coverage (of 15 total): {len(uncovered)} -> {uncovered}")

# ============================== 2. HARD NEGATIVES ==============================
print("\n" + "=" * 100)
print("AUDIT 2: HARD NEGATIVES (wrong_type / reversed_direction / false_pair)")
print("=" * 100)

# ---- wrong_type ----
print("\n---- wrong_type ----")
wrong_type_v1_total = wrong_type_v2_usable = wrong_type_questionable = 0
for ex in V1_EXAMPLES:
    if ex.kind != "wrong_type":
        continue
    wrong_type_v1_total += 1
    case = next(c for c in CASES_WITH_EDGES if c.case_id == ex.case_id)
    source = node_for(case, ex.from_ref)
    target = node_for(case, ex.to_ref)
    counts = atom_kind_counts(case)
    key = (ex.case_id, ex.from_ref, ex.to_ref)
    unambig = referent_unambiguous(source, counts) and referent_unambiguous(target, counts)
    v2_hyp = verbalize(ex.relation_type, source, target) if unambig else UNSUPPORTED_FOR_NLI
    questionable = key in QUESTIONABLE_WRONG_TYPE
    valid = (v2_hyp != UNSUPPORTED_FOR_NLI) and not questionable
    if v2_hyp != UNSUPPORTED_FOR_NLI:
        if questionable:
            wrong_type_questionable += 1
            verdict = "QUESTIONABLE — see QUESTIONABLE_WRONG_TYPE (manually reviewed: not clearly false for this premise)"
        else:
            wrong_type_v2_usable += 1
            verdict = "valid negative"
    else:
        verdict = "UNSUPPORTED_FOR_NLI (no verbalizer for this wrong type on this node-kind pair, or ambiguous referent)"
    print(f"  {ex.case_id}: {ex.from_ref}->{ex.to_ref} wrong_type={ex.relation_type} -> v2={v2_hyp!r} [{verdict}]")

print(f"\nwrong_type: v1 total={wrong_type_v1_total} v2 usable={wrong_type_v2_usable} "
      f"questionable={wrong_type_questionable} dropped(unsupported)={wrong_type_v1_total - wrong_type_v2_usable - wrong_type_questionable}")

# ---- reversed_direction ----
print("\n---- reversed_direction ----")
reversed_v1_total = reversed_v2_usable = reversed_invalid_symmetric = 0
for ex in V1_EXAMPLES:
    if ex.kind != "reversed_direction":
        continue
    reversed_v1_total += 1
    case = next(c for c in CASES_WITH_EDGES if c.case_id == ex.case_id)
    is_symmetric = ex.relation_type in SYMMETRIC_RELATION_TYPES
    if is_symmetric:
        reversed_invalid_symmetric += 1
        print(f"  {ex.case_id}: {ex.from_ref}<->{ex.to_ref} relation_type={ex.relation_type} "
              f"[INVALID — symmetric relation_type, reversed pair is ALSO true, not a negative]")
        continue
    source = node_for(case, ex.from_ref)
    target = node_for(case, ex.to_ref)
    counts = atom_kind_counts(case)
    unambig = referent_unambiguous(source, counts) and referent_unambiguous(target, counts)
    v2_hyp = verbalize(ex.relation_type, source, target) if unambig else UNSUPPORTED_FOR_NLI
    if v2_hyp != UNSUPPORTED_FOR_NLI:
        reversed_v2_usable += 1
        print(f"  {ex.case_id}: {ex.from_ref}<->{ex.to_ref} relation_type={ex.relation_type} -> v2={v2_hyp!r} [valid negative]")
    else:
        print(f"  {ex.case_id}: {ex.from_ref}<->{ex.to_ref} relation_type={ex.relation_type} [UNSUPPORTED_FOR_NLI]")

print(f"\nreversed_direction: v1 total={reversed_v1_total} invalid(symmetric)={reversed_invalid_symmetric} "
      f"v2 usable={reversed_v2_usable} dropped(unsupported)={reversed_v1_total - reversed_invalid_symmetric - reversed_v2_usable}")

# ---- false_pair ----
print("\n---- false_pair ----")
false_pair_v1_total = sum(1 for ex in V1_EXAMPLES if ex.kind == "false_pair")
cases_with_forbidden = [c for c in GOLDEN_CASES if c.forbidden_edges]
print(f"v1 false_pair instances (absence-from-case.edges heuristic, NOT owner-approved): {false_pair_v1_total}")
print(f"cases with EXPLICIT ForbiddenEdge (the only owner-approved source of a proven false pair): "
      f"{len(cases_with_forbidden)} -> {[c.case_id for c in cases_with_forbidden]}")

false_pair_v2_usable = 0
for case in cases_with_forbidden:
    for fe in case.forbidden_edges:
        source = node_for(case, fe.from_ref)
        target = node_for(case, fe.to_ref)
        for relation_type in sorted(all_relation_types):
            v2_hyp = verbalize(relation_type, source, target)
            if v2_hyp != UNSUPPORTED_FOR_NLI:
                false_pair_v2_usable += 1
                print(f"  {case.case_id}: {fe.from_ref}->{fe.to_ref} relation_type={relation_type} "
                      f"-> v2={v2_hyp!r} [valid negative — text explicitly denies ANY relation]")
print(f"\nfalse_pair: v1 total={false_pair_v1_total} (methodologically UNPROVEN per owner's rule) "
      f"v2 usable(from ForbiddenEdge only)={false_pair_v2_usable}")

print("\n" + "=" * 100)
print("FINAL v2 DATASET SIZE")
print("=" * 100)
print(f"positives: {pos_v2_usable}")
print(f"hard negatives: wrong_type={wrong_type_v2_usable} reversed_direction={reversed_v2_usable} "
      f"false_pair={false_pair_v2_usable}")
total_negatives = wrong_type_v2_usable + reversed_v2_usable + false_pair_v2_usable
print(f"total hard negatives: {total_negatives}")
print(f"TOTAL v2 dataset: {pos_v2_usable + total_negatives}")
