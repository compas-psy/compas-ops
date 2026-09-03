#!/usr/bin/env python3
"""R4.6.F1.2 — offline (без NLI) проверка: сколько из 37 GoldEdge позитивов
`RelationVerbalizerV3` (quoted reference) может процитировать однозначно и
грамматически валидно, в сравнении с v2 (родовая ссылка, `UNSUPPORTED_FOR_NLI`
для любого атома, чей kind неоднозначен в кейсе). Не часть замороженного v3
benchmark — только диагностика самого verbalizer'а на исторических данных,
включая специально упомянутые владельцем `resulted_in`/`supports`.

Запуск: `python3 scripts/r4-f1-2-verbalizer-v3-recovery-check.py` из
`helm/tree/control-plane` (нужен `helm_core` на PYTHONPATH — либо
`PYTHONPATH=. python3 ../scripts/r4-f1-2-verbalizer-v3-recovery-check.py`
из корня control-plane)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "control-plane"))

from helm_core.knowledge import relation_verbalizer_v2 as v2
from helm_core.knowledge import relation_verbalizer_v3 as v3
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES, GoldenCase


def _node_v2(case: GoldenCase, ref: str) -> v2.Node:
    for e in case.entities:
        if e.ref == ref:
            return v2.Node(category="ENTITY", ref_kind=e.entity_type, label=e.label)
    for a in case.atoms:
        if a.ref == ref:
            return v2.Node(category="ATOM", ref_kind=a.kind, label=a.canonical_text)
    raise KeyError(ref)


def _node_v3(case: GoldenCase, ref: str) -> v3.Node:
    for e in case.entities:
        if e.ref == ref:
            return v3.Node(category="ENTITY", ref_kind=e.entity_type, label=e.label)
    for a in case.atoms:
        if a.ref == ref:
            return v3.Node(category="ATOM", ref_kind=a.kind, label=a.canonical_text)
    raise KeyError(ref)


def _atom_kind_ambiguous_v2(case: GoldenCase, ref: str) -> bool:
    """v2's actual gate (implemented in the F1.1 audit script, reproduced
    here so this check is self-contained): a generic atom reference is
    unusable if более одного атома кейса делят тот же kind."""
    for a in case.atoms:
        if a.ref == ref:
            same_kind = [x for x in case.atoms if x.kind == a.kind]
            return len(same_kind) > 1
    return False


def main() -> int:
    total = 0
    v2_ok = 0
    v3_ok = 0
    by_type: dict[str, list[str]] = {}
    for case in GOLDEN_CASES:
        for edge in case.edges:
            total += 1
            na2, nb2 = _node_v2(case, edge.from_ref), _node_v2(case, edge.to_ref)
            na3, nb3 = _node_v3(case, edge.from_ref), _node_v3(case, edge.to_ref)

            v2_hyp = v2.UNSUPPORTED_FOR_NLI
            if edge.from_ref not in {a.ref for a in case.atoms} or not _atom_kind_ambiguous_v2(case, edge.from_ref):
                if edge.to_ref not in {a.ref for a in case.atoms} or not _atom_kind_ambiguous_v2(case, edge.to_ref):
                    v2_hyp = v2.verbalize(edge.relation_type, na2, nb2)
            v3_hyp = v3.verbalize(edge.relation_type, na3, nb3)

            ok2 = v2_hyp != v2.UNSUPPORTED_FOR_NLI
            ok3 = v3_hyp != v3.UNSUPPORTED_FOR_NLI
            v2_ok += ok2
            v3_ok += ok3
            by_type.setdefault(edge.relation_type, []).append("v2✓v3✓" if ok2 and ok3 else
                                                                "v2✗v3✓" if ok3 else
                                                                "v2✓v3✗" if ok2 else "v2✗v3✗")
            marker = "RECOVERED" if (ok3 and not ok2) else ("regressed" if (ok2 and not ok3) else "")
            print(f"{case.case_id:32s} {edge.relation_type:12s} {edge.from_ref}->{edge.to_ref:4s} "
                  f"v2={'ok' if ok2 else 'UNSUPPORTED':11s} v3={'ok' if ok3 else 'UNSUPPORTED':11s} {marker}")
            if ok3:
                print(f"    v3: {v3_hyp}")

    print()
    print(f"TOTAL gold edges: {total}")
    print(f"v2 usable: {v2_ok}/{total}")
    print(f"v3 usable: {v3_ok}/{total}")
    print()
    print("По relation_type (v2✓v3✓ / v2✗v3✓=RECOVERED / v2✓v3✗=regressed / v2✗v3✗):")
    for rt, statuses in sorted(by_type.items()):
        counts = {s: statuses.count(s) for s in set(statuses)}
        print(f"  {rt:12s} n={len(statuses):2d}  {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
