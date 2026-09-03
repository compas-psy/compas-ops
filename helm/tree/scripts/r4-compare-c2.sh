#!/bin/bash
# HELM v4.0 RESCUE · R4.6.C2 — сравнить deterministic candidate
# generation + relation-or-NONE classifier с single-pass (R4.6.B) и
# two-pass (R4.6.C) baseline на ТОМ ЖЕ golden-подмножестве (14 кейсов с
# gold-связями, кроме long_dense_window — R4.6.A дважды подтвердил
# детерминированный timeout независимо от архитектуры извлечения).
#
# Offline-пересчёт под typed-метрикой (R4.6.B.1, из уже сохранённых
# job-логов run 217/220, без повторного вызова Ollama):
#   single-pass (run 217): typed relation_precision=0.000, recall=0.000
#   two-pass    (run 220): typed relation_precision=0.133, recall=0.182
#
# Использует ТУ ЖЕ evaluate_case()/aggregate(), что и штатный
# r4-golden-benchmark.sh — метрики сравнимы напрямую. Инструментирует
# ВРУЧНУЮ (generate_candidates()/classify_relation() по отдельности,
# не через extract_window_c2()) только ради счётчиков NONE/candidates —
# сам путь склейки идентичен production-модулю semantic_extract_c2.py.
set -uo pipefail
cd /opt/helm/compose

PREEXISTING_MODELS=$(sudo docker compose exec -T ollama ollama list | tail -n +2 | awk '{print $1}')
OLLAMA_CID() { sudo docker compose ps -q ollama; }
ORIGINAL_MEM_LIMIT=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$(OLLAMA_CID)")
if [ "$ORIGINAL_MEM_LIMIT" = "0" ] || [ -z "$ORIGINAL_MEM_LIMIT" ]; then
  ORIGINAL_MEM_LIMIT_HUMAN="4g"
else
  ORIGINAL_MEM_LIMIT_HUMAN="${ORIGINAL_MEM_LIMIT}b"
fi
echo "=== временно поднимаем лимит ollama до 8g ==="
sudo docker update --memory=8g --memory-swap=8g "$(OLLAMA_CID)"
echo "=== ollama pull qwen2.5:7b ==="
sudo docker compose exec -T ollama ollama pull qwen2.5:7b

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
import time

from helm_core.knowledge.relation_candidates import generate_candidates
from helm_core.knowledge.relation_classifier import classify_relation
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_benchmark_metrics import aggregate, evaluate_case
from helm_core.knowledge.semantic_extract import ExtractionFailed, WindowExtraction, WindowTruncated, extract_window

SKIP = {"long_dense_window"}
MODEL = "qwen2.5:7b"
KEEP_ALIVE = "0"

cases = [c for c in GOLDEN_CASES if c.edges and c.case_id not in SKIP]
print(f"кейсов: {len(cases)}, всего gold-связей: {sum(len(c.edges) for c in cases)}")
print()

scores = []
total_candidates = total_none = total_rejected = total_accepted = 0
for case in cases:
    t0 = time.monotonic()
    try:
        pass1 = extract_window(case.text, domain=case.domain, heading_path=case.heading_path,
                               model=MODEL, keep_alive=KEEP_ALIVE)
    except (WindowTruncated, ExtractionFailed) as exc:
        dt = time.monotonic() - t0
        print(f"{case.case_id}: ПРОПУЩЕН (pass 1) за {dt:.1f}с — {exc}")
        continue

    objects_by_id = {e.local_id: e for e in pass1.entities}
    objects_by_id.update({a.local_id: a for a in pass1.atoms})
    candidates = generate_candidates(pass1.entities, pass1.atoms, case.text)

    edges, rejected, none_count = [], list(pass1.rejected), 0
    for candidate in candidates:
        edge, reason = classify_relation(
            candidate, from_obj=objects_by_id[candidate.from_id], to_obj=objects_by_id[candidate.to_id],
            model=MODEL, keep_alive=KEEP_ALIVE)
        if edge is not None:
            edges.append(edge)
        elif reason is not None:
            rejected.append(reason)
        else:
            none_count += 1

    dt = time.monotonic() - t0
    extraction = WindowExtraction(entities=pass1.entities, atoms=pass1.atoms, edges=edges, rejected=rejected)
    score = evaluate_case(case, extraction)
    scores.append(score)
    total_candidates += len(candidates)
    total_none += none_count
    total_rejected += len(rejected) - len(pass1.rejected)
    total_accepted += len(edges)
    print(f"{case.case_id}: {dt:.1f}с — entities={len(extraction.entities)} atoms={len(extraction.atoms)} "
          f"candidates={len(candidates)} accepted={len(edges)} none={none_count} "
          f"rejected={len(rejected) - len(pass1.rejected)} — "
          f"edges_matched={score.edges_matched}/{score.edges_gold_scoreable} "
          f"relation_type_correct={score.relation_type_correct} extra={score.edges_extracted_extra}")

print()
print("########## ИТОГО (C2, то же подмножество, что single-pass run 217 / two-pass run 220) ##########")
agg = aggregate(scores)
typed_matched = sum(s.relation_type_correct for s in scores)
typed_extra = sum(s.edges_extracted_extra for s in scores) + \
    (sum(s.edges_matched for s in scores) - typed_matched)
print(f"  кейсов оценено: {agg.cases_scored} из {len(cases)}")
print(f"  всего candidates={total_candidates} accepted={total_accepted} none={total_none} rejected={total_rejected}")
print(f"  typed relation_precision (нормативная, R4.6.B.1): {agg.relation_precision:.3f}")
print(f"  typed relation_recall: {agg.relation_recall:.3f}")
print(f"  typed relation_f1: {agg.relation_f1:.3f}")
print(f"  endpoint_relation_precision (диагностика): {agg.endpoint_relation_precision:.3f}")
print(f"  endpoint_relation_recall (диагностика): {agg.endpoint_relation_recall:.3f}")
print(f"  relation_type_accuracy: {agg.relation_type_accuracy:.3f}")
print(f"  entity_precision/recall: {agg.entity_precision:.3f}/{agg.entity_recall:.3f}")
print(f"  atom_precision/recall: {agg.atom_precision:.3f}/{agg.atom_recall:.3f}")
print(f"  total_material_hallucinations: {agg.total_material_hallucinations}")
print(f"  critical_entity_event_recall: {agg.critical_entity_event_recall:.3f}")
print()
print("  для сравнения — offline typed-пересчёт (R4.6.B.1, без повторного вызова Ollama):")
print("    single-pass (run 217): typed_precision=0.000 typed_recall=0.000 relation_type_accuracy=0.000")
print("    two-pass    (run 220): typed_precision=0.133 typed_recall=0.182 relation_type_accuracy=0.800")
print()
print("  GO/NO-GO (владелец): typed relation_precision >= 0.90 И recall не коллапсирует")
if agg.relation_precision >= 0.90:
    print(f"    -> GO (precision {agg.relation_precision:.3f} >= 0.90) — кандидат идёт на полный R4 golden")
else:
    print(f"    -> NO-GO (precision {agg.relation_precision:.3f} < 0.90) — C2 STOP, больше не тюнить промпт qwen2.5:7b")
PYEOF
diag_rc=$?

echo
echo "############ ВОССТАНОВЛЕНИЕ ИСХОДНОГО СОСТОЯНИЯ OLLAMA ############"
current_models=$(sudo docker compose exec -T ollama ollama list | tail -n +2 | awk '{print $1}')
for m in $current_models; do
  found=0
  for p in $PREEXISTING_MODELS; do
    [ "$m" = "$p" ] && found=1 && break
  done
  if [ "$found" -eq 0 ]; then
    sudo docker compose exec -T ollama ollama rm "$m" >/dev/null 2>&1 || true
  fi
done
sudo docker update --memory="$ORIGINAL_MEM_LIMIT_HUMAN" --memory-swap="$ORIGINAL_MEM_LIMIT_HUMAN" "$(OLLAMA_CID)"

if [ "$diag_rc" -ne 0 ]; then
  echo "::error::диагностика завершилась с кодом $diag_rc"
  exit "$diag_rc"
fi
