#!/usr/bin/env python3
"""R4.6.F1.2 — offline проверка freeze-контракта `relation_benchmark_v3_fixtures`:
каждый declared edge (entailed И not_entailed) должен быть verbalizable
`RelationVerbalizerV3` (иначе для него нет NLI-примера); покрытие
≥6 positive / ≥3 case_id на relation_type (15/15); агрегатное
not_entailed/entailed ≥2; per-split totals. Печатает markdown-таблицу
для freeze-дампа."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "control-plane"))

from helm_core.knowledge import relation_verbalizer_v3 as v3
from helm_core.knowledge.relation_benchmark_v3_fixtures import RELATION_BENCHMARK_V3_CASES
from helm_core.models.base import SemanticRelationType


def _node(case, ref: str) -> v3.Node:
    for e in case.entities:
        if e.ref == ref:
            return v3.Node(category="ENTITY", ref_kind=e.entity_type, label=e.label)
    for a in case.atoms:
        if a.ref == ref:
            return v3.Node(category="ATOM", ref_kind=a.kind, label=a.canonical_text)
    raise KeyError(f"{case.case_id}: {ref!r} not found")


def main() -> int:
    all_types = {m.value for m in SemanticRelationType}
    pos_by_type: dict[str, int] = defaultdict(int)
    neg_by_type: dict[str, int] = defaultdict(int)
    cases_by_type: dict[str, set[str]] = defaultdict(set)
    unverbalizable: list[str] = []
    total_pos = total_neg = 0
    by_split: dict[str, dict[str, int]] = defaultdict(lambda: {"cases": 0, "pos": 0, "neg": 0})

    for case in RELATION_BENCHMARK_V3_CASES:
        by_split[case.split]["cases"] += 1
        for p in case.entailed:
            total_pos += 1
            pos_by_type[p.relation_type] += 1
            cases_by_type[p.relation_type].add(case.case_id)
            by_split[case.split]["pos"] += 1
            hyp = v3.verbalize(p.relation_type, _node(case, p.from_ref), _node(case, p.to_ref))
            if hyp == v3.UNSUPPORTED_FOR_NLI:
                unverbalizable.append(f"POSITIVE {case.case_id} {p.from_ref}-{p.relation_type}->{p.to_ref}")
        for n in case.not_entailed:
            total_neg += 1
            neg_by_type[n.relation_type] += 1
            by_split[case.split]["neg"] += 1
            hyp = v3.verbalize(n.relation_type, _node(case, n.from_ref), _node(case, n.to_ref))
            if hyp == v3.UNSUPPORTED_FOR_NLI:
                unverbalizable.append(f"NEGATIVE {case.case_id} {n.from_ref}-{n.relation_type}->{n.to_ref}")

    print("## Coverage matrix (15/15)\n")
    print("| relation_type | positives | distinct case_id | negatives | positives>=6 | case_id>=3 |")
    print("|---|---|---|---|---|---|")
    ok = True
    for rt in sorted(all_types):
        p, n, c = pos_by_type[rt], neg_by_type[rt], len(cases_by_type[rt])
        p_ok, c_ok = p >= 6, c >= 3
        ok = ok and p_ok and c_ok
        print(f"| {rt} | {p} | {c} | {n} | {'OK' if p_ok else 'FAIL'} | {'OK' if c_ok else 'FAIL'} |")

    print(f"\nTOTAL cases: {len(RELATION_BENCHMARK_V3_CASES)}")
    print(f"TOTAL positives: {total_pos}")
    print(f"TOTAL negatives: {total_neg}  (ratio negatives/positives = {total_neg/total_pos:.2f})")
    for split, d in sorted(by_split.items()):
        print(f"  split={split}: cases={d['cases']} positives={d['pos']} negatives={d['neg']}")

    print(f"\nUnverbalizable edges (MUST be empty): {len(unverbalizable)}")
    for u in unverbalizable:
        print(f"  {u}")

    print(f"\nAll 15 types >=6 positives and >=3 case_id: {'YES' if ok else 'NO'}")
    print(f"Missing from registry entirely: {sorted(all_types - set(pos_by_type))}")
    return 0 if (ok and not unverbalizable) else 1


if __name__ == "__main__":
    raise SystemExit(main())
