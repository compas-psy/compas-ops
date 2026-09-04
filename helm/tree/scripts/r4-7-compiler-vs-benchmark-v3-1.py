#!/usr/bin/env python3
"""R4.7 v3.1 (владелец 04.09.2026) — ОДИН recompute deterministic compiler
на `RELATION_BENCHMARK_V3_1_CASES` (frozen v3 + `V3_1_ERRATA`, 5 добавленных
`entailed`-кортежей в 3 case_id — см. `relation_benchmark_v3_1_errata.py`).

Идентичная методология оценке `r4-7-compiler-vs-benchmark-v3.py` (frozen
v3, оставлен без изменений как исторический артефакт — этот скрипт его
не заменяет, а дополняет отдельным прогоном на v3.1):
  TP = произведённое ребро совпадает с объявленным `entailed`.
  FP = произведённое ребро НЕ совпадает ни с одним `entailed`.
  gold miss = `entailed`, для которого compiler ничего не произвёл.

Владелец: «Ровно один recompute deterministic compiler на v3.1.» — этот
прогон не повторяется и не калибруется по своему же результату."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "control-plane"))

from helm_core.knowledge.relation_benchmark_v3_1_errata import RELATION_BENCHMARK_V3_1_CASES
from helm_core.knowledge.relation_compiler import compile_relations
from helm_core.knowledge.semantic_extract import ExtractedAtom, ExtractedEntity


def to_extracted(case):
    atom_text_by_ref = {a.ref: a.canonical_text for a in case.atoms}
    entity_evidence: dict[str, str] = {}
    for edge in case.entailed:
        if edge.to_ref not in entity_evidence and edge.from_ref in atom_text_by_ref:
            entity_evidence[edge.to_ref] = atom_text_by_ref[edge.from_ref]

    entities = [ExtractedEntity(local_id=e.ref, entity_type=e.entity_type, label=e.label,
                                evidence_quote=entity_evidence.get(e.ref, e.label))
               for e in case.entities]
    atoms = [ExtractedAtom(local_id=a.ref, kind=a.kind, title=a.canonical_text[:20],
                           text=a.canonical_text, evidence_quote=a.canonical_text)
            for a in case.atoms]
    return entities, atoms


def main() -> int:
    total_gold = total_produced = total_tp = total_fp = 0
    by_type_gold: dict[str, int] = defaultdict(int)
    by_type_tp: dict[str, int] = defaultdict(int)
    by_split = defaultdict(lambda: {"gold": 0, "produced": 0, "tp": 0, "fp": 0})

    for case in RELATION_BENCHMARK_V3_1_CASES:
        entities, atoms = to_extracted(case)
        produced = compile_relations(entities, atoms, case.text)
        produced_keys = [(e.from_local_id, e.relation_type, e.to_local_id) for e in produced]
        entailed_keys = {(p.from_ref, p.relation_type, p.to_ref) for p in case.entailed}
        not_entailed_keys = {(n.from_ref, n.relation_type, n.to_ref) for n in case.not_entailed}
        reason_by_key = {(n.from_ref, n.relation_type, n.to_ref): n.reason for n in case.not_entailed}

        tp = [k for k in produced_keys if k in entailed_keys]
        fp = [k for k in produced_keys if k not in entailed_keys]
        missed = entailed_keys - set(produced_keys)

        total_gold += len(entailed_keys)
        total_produced += len(produced_keys)
        total_tp += len(tp)
        total_fp += len(fp)
        by_split[case.split]["gold"] += len(entailed_keys)
        by_split[case.split]["produced"] += len(produced_keys)
        by_split[case.split]["tp"] += len(tp)
        by_split[case.split]["fp"] += len(fp)

        for (_, rt, _) in entailed_keys:
            by_type_gold[rt] += 1
        for (_, rt, _) in tp:
            by_type_tp[rt] += 1

        print(f"{case.case_id:32s} [{case.split}] gold={len(entailed_keys):2d} "
              f"produced={len(produced_keys):2d} tp={len(tp):2d} fp={len(fp):2d} missed={len(missed):2d}")
        for k in sorted(fp):
            reason = reason_by_key.get(k)
            tag = "explicit not_entailed" if k in not_entailed_keys else "UNDECLARED (neither list)"
            print(f"    FP ({tag}): {k}" + (f" — {reason}" if reason else ""))
        for k in sorted(missed):
            print(f"    missed: {k}")

    precision = total_tp / total_produced if total_produced else float("nan")
    recall = total_tp / total_gold if total_gold else float("nan")
    print()
    print(f"TOTAL entailed (gold, v3.1): {total_gold}")
    print(f"TOTAL produced: {total_produced}")
    print(f"TOTAL TP: {total_tp}  TOTAL FP: {total_fp}")
    print(f"precision (TP/produced) = {precision:.3f}")
    print(f"recall (TP/gold) = {recall:.3f}  (диагностика, не гейт — не оптимизировать)")
    print()
    print("По split:")
    for split, d in sorted(by_split.items()):
        p = d["tp"] / d["produced"] if d["produced"] else float("nan")
        r = d["tp"] / d["gold"] if d["gold"] else float("nan")
        print(f"  {split:16s} gold={d['gold']:3d} produced={d['produced']:3d} "
              f"tp={d['tp']:3d} fp={d['fp']:3d} precision={p:.3f} recall={r:.3f}")
    print()
    print("По relation_type (gold / TP):")
    for rt in sorted(set(by_type_gold) | set(by_type_tp)):
        print(f"  {rt:12s} gold={by_type_gold[rt]:2d} tp={by_type_tp[rt]:2d}")
    print()
    print(f"GATE (typed precision >= 0.90, v3.1, ровно один recompute): "
          f"{'PASS' if precision >= 0.90 else 'FAIL'} ({precision:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
