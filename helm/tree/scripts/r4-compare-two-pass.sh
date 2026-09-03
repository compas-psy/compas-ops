#!/bin/bash
# HELM v4.0 RESCUE · R4.6.C — сравнить two-pass extraction с single-pass
# baseline на ТОМ ЖЕ golden-подмножестве, что использовал R4.6.B (14
# кейсов с gold-связями, кроме long_dense_window — R4.6.A дважды
# подтвердил детерминированный timeout независимо от архитектуры
# извлечения, третий прогон не даст новых данных).
#
# Single-pass baseline с этого же подмножества (run 217,
# r4-diagnose-relation-precision.sh): relation_precision=7/23=0.304,
# correct=0, wrong_type=7, wrong_endpoints=10, missing=5,
# unscoreable=6, extra_relation=11, extra_due_to_entity_mismatch=5.
#
# Использует ТУ ЖЕ evaluate_case()/aggregate(), что и штатный
# r4-golden-benchmark.sh — метрики сравнимы напрямую, не отдельная
# копия подсчёта.
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

from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_benchmark_metrics import aggregate, evaluate_case
from helm_core.knowledge.semantic_extract import ExtractionFailed, WindowTruncated
from helm_core.knowledge.semantic_extract_twopass import extract_window_two_pass

SKIP = {"long_dense_window"}
MODEL = "qwen2.5:7b"
KEEP_ALIVE = "0"

cases = [c for c in GOLDEN_CASES if c.edges and c.case_id not in SKIP]
print(f"кейсов: {len(cases)}, всего gold-связей: {sum(len(c.edges) for c in cases)}")
print()

scores = []
for case in cases:
    t0 = time.monotonic()
    try:
        extraction = extract_window_two_pass(case.text, domain=case.domain, heading_path=case.heading_path,
                                             model=MODEL, keep_alive=KEEP_ALIVE)
    except (WindowTruncated, ExtractionFailed) as exc:
        dt = time.monotonic() - t0
        print(f"{case.case_id}: ПРОПУЩЕН за {dt:.1f}с — {exc}")
        continue
    dt = time.monotonic() - t0
    score = evaluate_case(case, extraction)
    scores.append(score)
    print(f"{case.case_id}: {dt:.1f}с — entities={len(extraction.entities)} atoms={len(extraction.atoms)} "
          f"edges={len(extraction.edges)} rejected={len(extraction.rejected)} — "
          f"edges_matched={score.edges_matched}/{score.edges_gold_scoreable} "
          f"relation_type_correct={score.relation_type_correct} extra={score.edges_extracted_extra}")

print()
print("########## ИТОГО (two-pass, то же подмножество, что и single-pass run 217) ##########")
agg = aggregate(scores)
print(f"  кейсов оценено: {agg.cases_scored} из {len(cases)}")
print(f"  relation_precision: {agg.relation_precision:.3f}")
print(f"  relation_recall: {agg.relation_recall:.3f}")
print(f"  relation_type_accuracy: {agg.relation_type_accuracy:.3f}")
print(f"  entity_precision/recall: {agg.entity_precision:.3f}/{agg.entity_recall:.3f}")
print(f"  atom_precision/recall: {agg.atom_precision:.3f}/{agg.atom_recall:.3f}")
print(f"  total_material_hallucinations: {agg.total_material_hallucinations}")
print(f"  critical_entity_event_recall: {agg.critical_entity_event_recall:.3f}")
print()
print("  для сравнения — single-pass baseline (run 217, то же подмножество):")
print("    relation_precision=0.304 (7/23), correct=0, wrong_type=7, wrong_endpoints=10,")
print("    missing=5, unscoreable=6, extra_relation=11, extra_due_to_entity_mismatch=5")
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
