#!/usr/bin/env python3
"""R4.7 — offline (без сервера) проверка: если бы pass1 extraction была
ИДЕАЛЬНОЙ (entities/atoms точно равны GOLDEN_CASES gold), что произвёл бы
deterministic relation compiler и как это соотносится с gold edges?
Изолирует корректность compiler'а от качества pass1 extraction — именно
то, что нужно проверить ПЕРЕД тратой единственного финального R4 прогона.

Не является заменой самого R4 прогона (там entities/atoms — из реальной
LLM, не идеальные) — только pre-flight на логику компилятора."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "control-plane"))

from helm_core.knowledge.relation_compiler import compile_relations
from helm_core.knowledge.semantic_extract import ExtractedAtom, ExtractedEntity
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES


def to_extracted(case):
    """`GoldEntity` не несёт evidence_quote (его в этой fixture-модели
    вообще нет) — но настоящий extractor цитирует КОНКРЕТНЫЙ атом, где
    он нашёл сущность, не голый label. Синтетическое приближение,
    честное, не подгонка: evidence сущности = canonical_text ПЕРВОГО
    gold-атома, который на неё ссылается через edge (если такого нет —
    остаётся label). Без этого `same_label_different_entities` не
    воспроизводим офлайн вообще — обе одноимённые сущности склеятся по
    голому совпадению фамилии, что не отражает то, как реально работает
    grounded evidence_quote в production extractor'е."""
    atom_text_by_ref = {a.ref: a.canonical_text for a in case.atoms}
    entity_evidence: dict[str, str] = {}
    for edge in case.edges:
        if edge.to_ref not in entity_evidence and edge.from_ref in atom_text_by_ref:
            entity_evidence[edge.to_ref] = atom_text_by_ref[edge.from_ref]

    entities = [ExtractedEntity(local_id=e.ref, entity_type=e.entity_type, label=e.label,
                                aliases=e.aliases, evidence_quote=entity_evidence.get(e.ref, e.label))
               for e in case.entities]
    atoms = [ExtractedAtom(local_id=a.ref, kind=a.kind, title=a.canonical_text[:20],
                           text=a.canonical_text, evidence_quote=a.canonical_text)
            for a in case.atoms]
    return entities, atoms


def main() -> int:
    total_gold = 0
    total_produced = 0
    total_matched = 0
    total_typed_correct = 0
    total_extra = 0
    total_forbidden_violations = 0
    by_type_gold: dict[str, int] = {}
    by_type_matched: dict[str, int] = {}

    for case in GOLDEN_CASES:
        if not case.edges and not case.forbidden_edges:
            continue
        entities, atoms = to_extracted(case)
        produced = compile_relations(entities, atoms, case.text)
        produced_keys = {(e.from_local_id, e.relation_type, e.to_local_id) for e in produced}
        gold_keys = {(e.from_ref, e.relation_type, e.to_ref) for e in case.edges}

        matched = produced_keys & gold_keys
        extra = produced_keys - gold_keys
        missed = gold_keys - produced_keys

        total_gold += len(gold_keys)
        total_produced += len(produced_keys)
        total_matched += len(matched)
        total_typed_correct += len(matched)
        total_extra += len(extra)

        for (_, rt, _) in gold_keys:
            by_type_gold[rt] = by_type_gold.get(rt, 0) + 1
        for (_, rt, _) in matched:
            by_type_matched[rt] = by_type_matched.get(rt, 0) + 1

        forbidden_hits = []
        for f in case.forbidden_edges:
            for (frm, rt, to) in produced_keys:
                if frm == f.from_ref and to == f.to_ref:
                    forbidden_hits.append((frm, rt, to))
        total_forbidden_violations += len(forbidden_hits)

        print(f"{case.case_id:32s} gold={len(gold_keys):2d} produced={len(produced_keys):2d} "
              f"matched={len(matched):2d} extra={len(extra):2d} missed={len(missed):2d}"
              f"{'  !!FORBIDDEN HIT!!' if forbidden_hits else ''}")
        for e in sorted(extra):
            print(f"    EXTRA (false positive): {e}")
        for m in sorted(missed):
            print(f"    missed (recall gap):    {m}")

    precision = total_matched / total_produced if total_produced else float("nan")
    recall = total_matched / total_gold if total_gold else float("nan")
    print()
    print(f"TOTAL gold-scoreable edges: {total_gold}")
    print(f"TOTAL produced edges: {total_produced}")
    print(f"TOTAL matched (typed, exact from/to/type): {total_matched}")
    print(f"TOTAL extra (false positives): {total_extra}")
    print(f"TOTAL forbidden-edge violations: {total_forbidden_violations}")
    print(f"precision (matched/produced) = {precision:.3f}")
    print(f"recall (matched/gold) = {recall:.3f}  (диагностика, не гейт)")
    print()
    print("По типам (gold / matched):")
    for rt in sorted(set(by_type_gold) | set(by_type_matched)):
        print(f"  {rt:12s} gold={by_type_gold.get(rt,0):2d} matched={by_type_matched.get(rt,0):2d}")

    print()
    print(f"GATE (typed precision >= 0.90, идеальный pass1 input): "
          f"{'PASS' if precision >= 0.90 else 'FAIL'} ({precision:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
