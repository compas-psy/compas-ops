#!/usr/bin/env python3
"""R4.6.F1.2 — генерирует полный markdown-дамп замороженного relation
benchmark v3 (владелец п.9: «Markdown dump всех v3 case»). Печатает на
stdout — перенаправляется в docs/R4.6.F1.2-BENCHMARK-V3-FREEZE.md.
Детерминированно, без вызовов NLI/LLM — чистое отображение fixtures."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "control-plane"))

from helm_core.knowledge.relation_benchmark_v3_fixtures import RELATION_BENCHMARK_V3_CASES
from helm_core.models.base import SemanticRelationType


def main() -> int:
    cases = RELATION_BENCHMARK_V3_CASES
    calib = [c for c in cases if c.split == "calibration"]
    holdout = [c for c in cases if c.split == "final_holdout"]

    print("# R4.6.F1.2 — relation benchmark v3: freeze dump\n")
    print(
        "Сгенерировано `scripts/r4-f1-2-generate-freeze-dump.py` из "
        "`helm_core/knowledge/relation_benchmark_v3_fixtures.py` — детерминированно, "
        "без вызовов NLI/LLM. Владелец п.9: этот файл — часть freeze-артефактов; "
        "SHA коммита, фиксирующего эту версию, записан в "
        "`docs/KNOWLEDGE_MODELS.md` (раздел R4.6.F1.2).\n"
    )

    all_types = {m.value for m in SemanticRelationType}
    pos_by_type: dict[str, int] = defaultdict(int)
    neg_by_type: dict[str, int] = defaultdict(int)
    cases_by_type: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        for p in case.entailed:
            pos_by_type[p.relation_type] += 1
            cases_by_type[p.relation_type].add(case.case_id)
        for n in case.not_entailed:
            neg_by_type[n.relation_type] += 1

    print("## Coverage matrix (15/15 SemanticRelationType)\n")
    print("| relation_type | positives | distinct case_id | negatives |")
    print("|---|---|---|---|")
    for rt in sorted(all_types):
        print(f"| {rt} | {pos_by_type[rt]} | {len(cases_by_type[rt])} | {neg_by_type[rt]} |")
    total_pos = sum(len(c.entailed) for c in cases)
    total_neg = sum(len(c.not_entailed) for c in cases)
    print(f"\n**TOTAL: {len(cases)} cases, {total_pos} positives, {total_neg} negatives "
          f"(ratio {total_neg/total_pos:.2f}).**\n")
    print(f"**Split: calibration = {len(calib)} cases / "
          f"{sum(len(c.entailed) for c in calib)} positives; "
          f"final_holdout = {len(holdout)} cases / "
          f"{sum(len(c.entailed) for c in holdout)} positives "
          "(frozen — не меняется после первого inference).**\n")

    for split_name, split_cases in (("DEV/CALIBRATION", calib), ("FINAL_HOLDOUT", holdout)):
        print(f"\n---\n\n## {split_name} ({len(split_cases)} cases)\n")
        for case in split_cases:
            print(f"### `{case.case_id}` ({case.domain})\n")
            print(f"> {case.text}\n")
            if case.entities:
                print("**Entities:**\n")
                for e in case.entities:
                    subtype = f", subtype={e.subtype}" if e.subtype else ""
                    print(f"- `{e.ref}` {e.entity_type} — «{e.label}»{subtype}")
                print()
            if case.atoms:
                print("**Atoms:**\n")
                for a in case.atoms:
                    print(f"- `{a.ref}` {a.kind} — «{a.canonical_text}»")
                print()
            print("**Entailed (positive):**\n")
            for p in case.entailed:
                role = f" (role={p.role})" if p.role else ""
                print(f"- `{p.from_ref}` --**{p.relation_type}**{role}--> `{p.to_ref}`")
            print("\n**Explicitly NOT entailed (hard negative):**\n")
            for n in case.not_entailed:
                print(f"- `{n.from_ref}` --**{n.relation_type}**--> `{n.to_ref}` — {n.reason}")
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
