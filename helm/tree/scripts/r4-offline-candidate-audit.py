#!/usr/bin/env python3
"""R4.6.E шаг 1 (владелец 03.09.2026) — OFFLINE, без единого вызова Ollama.

Размечает кандидатов, которые `generate_candidates()` (relation_candidates.py)
породил бы на 14-case relation subset, ПРОТИВ gold-рёбер — используя не
реальный (шумный) вывод pass 1, а сами GOLD entities/atoms как объекты
кандидатогенерации. Это намеренно: цель — измерить combinatorial-поведение
ГЕНЕРАТОРА в изоляции от ошибок извлечения модели (гипотетически идеальный
pass 1) — какая доля кандидатов, порождённых КАЖДЫМ критерием близости
(overlap/mention/same_sentence/same_paragraph/adjacent_sentence), вообще
соответствует настоящей gold-связи.

Ограничение метода (честно, не скрывается): `GoldAtom.canonical_text` — это
"опорный текст" для семантического сравнения (см. docstring
semantic_benchmark_fixtures.py), не гарантированно дословная подстрока
`case.text` — в отличие от продакшен `ExtractedAtom.evidence_quote`, который
ОБЯЗАН быть grounded (R4.5.3). Атом/сущность, чей текст не найден дословно в
окне (после нормализации пробелов), пропускается генератором молча — тем же
путём, что и в продакшене для негаундированного evidence. Ниже это явно
считается и репортится как `unlocated`, а не скрывается.

Direction в этой разметке игнорируется (owner п.1: "true candidate"/"false
candidate" — вопрос СУЩЕСТВОВАНИЯ связи между парой, не направления;
направление — отдельная проблема C2, п.2D/3 мандата).
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, "/home/user/compas-ops/helm/tree/control-plane")

from helm_core.knowledge.relation_candidates import generate_candidates
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_extract import ExtractedAtom, ExtractedEntity

_WS = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _locatable(quote: str, window_norm: str) -> bool:
    return bool(quote) and _normalize_ws(quote) in window_norm


SKIP = {"long_dense_window"}
cases = [c for c in GOLDEN_CASES if c.edges and c.case_id not in SKIP]

REASONS = ["overlap", "mention", "same_sentence", "same_paragraph", "adjacent_sentence"]
per_reason = {r: {"candidates": 0, "true": 0, "false": 0} for r in REASONS}
total_gold_edges = 0
total_gold_covered = 0
total_unlocated_entities = 0
total_unlocated_atoms = 0
total_entities = 0
total_atoms = 0

print(f"кейсов: {len(cases)}, всего gold-связей: {sum(len(c.edges) for c in cases)}")
print()

for case in cases:
    window_norm = _normalize_ws(case.text)

    gold_entities = []
    unlocated_e = []
    for e in case.entities:
        total_entities += 1
        quote = e.label if _locatable(e.label, window_norm) else next(
            (a for a in e.aliases if _locatable(a, window_norm)), "")
        if not quote:
            unlocated_e.append(e.ref)
            total_unlocated_entities += 1
            continue
        gold_entities.append(ExtractedEntity(local_id=e.ref, entity_type=e.entity_type,
                                             label=e.label, aliases=e.aliases, evidence_quote=quote))

    gold_atoms = []
    unlocated_a = []
    for a in case.atoms:
        total_atoms += 1
        if not _locatable(a.canonical_text, window_norm):
            unlocated_a.append(a.ref)
            total_unlocated_atoms += 1
            continue
        gold_atoms.append(ExtractedAtom(local_id=a.ref, kind=a.kind, title="", text=a.canonical_text,
                                        evidence_quote=a.canonical_text))

    candidates = generate_candidates(gold_entities, gold_atoms, case.text)

    gold_pairs = {frozenset((edge.from_ref, edge.to_ref)) for edge in case.edges}
    total_gold_edges += len(gold_pairs)
    covered_pairs: set[frozenset] = set()

    case_counts = {r: {"candidates": 0, "true": 0, "false": 0} for r in REASONS}
    for cand in candidates:
        pair = frozenset((cand.from_id, cand.to_id))
        is_true = pair in gold_pairs
        case_counts[cand.reason]["candidates"] += 1
        case_counts[cand.reason]["true" if is_true else "false"] += 1
        per_reason[cand.reason]["candidates"] += 1
        per_reason[cand.reason]["true" if is_true else "false"] += 1
        if is_true:
            covered_pairs.add(pair)
    total_gold_covered += len(covered_pairs)

    case_total = len(candidates)
    case_true = sum(v["true"] for v in case_counts.values())
    note = ""
    if unlocated_e or unlocated_a:
        note = f" — unlocated: entities={unlocated_e or 'нет'} atoms={unlocated_a or 'нет'}"
    print(f"{case.case_id}: gold_edges={len(gold_pairs)} candidates={case_total} true={case_true} "
          f"false={case_total - case_true} covered={len(covered_pairs)}/{len(gold_pairs)}{note}")
    for r in REASONS:
        c = case_counts[r]
        if c["candidates"]:
            print(f"    {r}: candidates={c['candidates']} true={c['true']} false={c['false']}")

print()
print("########## ИТОГО по критерию близости (offline, gold objects, БЕЗ Ollama) ##########")
print(f"  entities: {total_entities} всего, {total_unlocated_entities} unlocated (пропущены)")
print(f"  atoms: {total_atoms} всего, {total_unlocated_atoms} unlocated (canonical_text не дословен — пропущены)")
print()
grand_candidates = grand_true = 0
for r in REASONS:
    c = per_reason[r]
    n = c["candidates"]
    grand_candidates += n
    grand_true += c["true"]
    precision = c["true"] / n if n else float("nan")
    print(f"  {r}: candidates={n} true={c['true']} false={c['false']} candidate_precision={precision:.3f}")
print()
overall_precision = grand_true / grand_candidates if grand_candidates else float("nan")
overall_recall = total_gold_covered / total_gold_edges if total_gold_edges else float("nan")
print(f"  ИТОГО candidates={grand_candidates} true={grand_true} false={grand_candidates - grand_true}")
print(f"  overall candidate_precision (потолок для classifier'а): {overall_precision:.3f}")
print(f"  overall candidate_recall (доля gold-рёбер, покрытых ХОТЯ БЫ одним кандидатом): "
      f"{total_gold_covered}/{total_gold_edges} = {overall_recall:.3f}")
