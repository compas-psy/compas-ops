#!/bin/bash
# HELM v4.0 RESCUE · R4.6.B — владелец 03.09.2026: разложить
# relation_precision=0.28 qwen2.5:7b (run 210) по типам ошибок:
# wrong relation type / wrong endpoints / missing relation / extra
# relation / relation rejected by evidence grounding / entity-atom
# mismatch делает связь unscoreable. Не лечить снижением порога —
# только классификация.
#
# run 210's result.json НЕ хранит сырую WindowExtraction (local_id-
# уровень edges/entities/atoms/rejected) — только агрегированный
# CaseScore и notes (см. semantic_benchmark.py: CaseRun не держит
# extraction). Разложить по этим шести категориям retroactive из
# существующего артефакта НЕЛЬЗЯ — данных физически нет (CLAUDE.md
# §5.1: «данных нет», а не оценка). Это targeted-прогон extract_window()
# напрямую на кейсах с golden edges, production policy keep_alive=0,
# захватывающий сырую extraction и классифицирующий каждую gold- и
# extra-связь через ТЕ ЖЕ match_entities/match_atoms, что использует
# сам харнесс (не отдельная копия логики сопоставления).
#
# long_dense_window сознательно ПРОПУЩЕН: R4.6.A дважды подтвердил
# детерминированный timeout (360.4с под keep_alive=0 И 5m) — третий
# прогон даст тот же результат, не даст новых данных, только потратит
# ~6 минут.
set -uo pipefail
cd /opt/helm/compose

PREEXISTING_MODELS=$(sudo docker compose exec -T ollama ollama list | tail -n +2 | awk '{print $1}')
echo "модели до диагностики:"
echo "$PREEXISTING_MODELS" | sed 's/^/  /'

OLLAMA_CID() { sudo docker compose ps -q ollama; }

ORIGINAL_MEM_LIMIT=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$(OLLAMA_CID)")
if [ "$ORIGINAL_MEM_LIMIT" = "0" ] || [ -z "$ORIGINAL_MEM_LIMIT" ]; then
  ORIGINAL_MEM_LIMIT_HUMAN="4g"
else
  ORIGINAL_MEM_LIMIT_HUMAN="${ORIGINAL_MEM_LIMIT}b"
fi
echo "лимит памяти до диагностики: $ORIGINAL_MEM_LIMIT_HUMAN"
echo "=== временно поднимаем лимит ollama до 8g (нужно для qwen2.5:7b) ==="
sudo docker update --memory=8g --memory-swap=8g "$(OLLAMA_CID)"

echo "=== ollama pull qwen2.5:7b ==="
sudo docker compose exec -T ollama ollama pull qwen2.5:7b

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
import time

from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_benchmark_metrics import (
    EXTRACTED_EDGES_CTX, GOLD_EDGES_CTX, match_atoms, match_entities,
)
from helm_core.knowledge.semantic_extract import ExtractionFailed, WindowTruncated, extract_window

SKIP = {"long_dense_window"}  # R4.6.A: детерминированный timeout, не даёт данных
MODEL = "qwen2.5:7b"
KEEP_ALIVE = "0"  # production policy

cases = [c for c in GOLDEN_CASES if c.edges and c.case_id not in SKIP]
print(f"кейсов с gold-связями (кроме {sorted(SKIP)}): {len(cases)}")
print(f"всего gold-связей в них: {sum(len(c.edges) for c in cases)}")
print()

totals = {
    "correct": 0, "wrong_type": 0, "wrong_endpoints": 0, "missing": 0,
    "unscoreable_entity_atom_mismatch": 0,
    "extra_relation": 0, "extra_due_to_entity_mismatch": 0,
    "rejected_by_grounding": 0, "rejected_other_structural": 0,
}

for case in cases:
    print(f"########## {case.case_id} ##########")
    t0 = time.monotonic()
    try:
        extraction = extract_window(case.text, domain=case.domain, heading_path=case.heading_path,
                                    model=MODEL, keep_alive=KEEP_ALIVE)
    except (WindowTruncated, ExtractionFailed) as exc:
        dt = time.monotonic() - t0
        print(f"  ПРОПУЩЕН — извлечение не удалось за {dt:.1f}с: {exc}")
        print(f"  gold-связи этого кейса ({len(case.edges)}) НЕ классифицированы — нет сырых данных, не оцениваем")
        print()
        continue
    dt = time.monotonic() - t0
    print(f"  извлечение за {dt:.1f}с — entities={len(extraction.entities)} atoms={len(extraction.atoms)} "
          f"edges={len(extraction.edges)} rejected={len(extraction.rejected)}")

    GOLD_EDGES_CTX.set(case.edges)
    EXTRACTED_EDGES_CTX.set(extraction.edges)
    try:
        entity_match = match_entities(case.entities, extraction.entities)
        atom_match = match_atoms(case.atoms, extraction.atoms)
    finally:
        GOLD_EDGES_CTX.set(())
        EXTRACTED_EDGES_CTX.set(())

    ref_to_local_id = {}
    for m in entity_match.matched:
        ref_to_local_id[m.gold.ref] = m.extracted.local_id
    for m in atom_match.matched:
        ref_to_local_id[m.gold.ref] = m.extracted.local_id
    unmatched_local_ids = ({e.local_id for e in entity_match.unmatched_extracted}
                           | {a.local_id for a in atom_match.unmatched_extracted})

    extracted_edge_set = {(e.from_local_id, e.to_local_id): e for e in extraction.edges}
    matched_extracted_keys = set()

    for edge in case.edges:
        if edge.from_ref not in ref_to_local_id or edge.to_ref not in ref_to_local_id:
            totals["unscoreable_entity_atom_mismatch"] += 1
            print(f"  UNSCOREABLE (entity/atom mismatch): {edge.from_ref} {edge.relation_type} {edge.to_ref} "
                  f"— {'from' if edge.from_ref not in ref_to_local_id else 'to'} не сопоставлен gold")
            continue
        from_lid, to_lid = ref_to_local_id[edge.from_ref], ref_to_local_id[edge.to_ref]
        ext = extracted_edge_set.get((from_lid, to_lid))
        if ext is not None:
            matched_extracted_keys.add((from_lid, to_lid))
            if ext.relation_type.strip().casefold() == edge.relation_type.strip().casefold():
                totals["correct"] += 1
            else:
                totals["wrong_type"] += 1
                print(f"  WRONG TYPE: {edge.from_ref} {edge.relation_type} {edge.to_ref} "
                      f"— extracted type={ext.relation_type!r}")
            continue
        # не найдено по точным local_id — проверяем, не связал ли модель
        # правильную сторону с ДРУГИМ объектом (или перепутала направление)
        touches_from = any(e.from_local_id == from_lid or e.to_local_id == from_lid for e in extraction.edges)
        touches_to = any(e.from_local_id == to_lid or e.to_local_id == to_lid for e in extraction.edges)
        if touches_from or touches_to:
            totals["wrong_endpoints"] += 1
            print(f"  WRONG ENDPOINTS: {edge.from_ref} {edge.relation_type} {edge.to_ref} "
                  f"— модель связала один из сопоставленных объектов с другим партнёром")
        else:
            totals["missing"] += 1
            print(f"  MISSING: {edge.from_ref} {edge.relation_type} {edge.to_ref} — ни одной связи не найдено вовсе")

    for (from_lid, to_lid), ext in extracted_edge_set.items():
        if (from_lid, to_lid) in matched_extracted_keys:
            continue
        if from_lid in unmatched_local_ids or to_lid in unmatched_local_ids:
            totals["extra_due_to_entity_mismatch"] += 1
            print(f"  EXTRA (из-за лишней сущности/атома): {from_lid} {ext.relation_type} {to_lid}")
        else:
            totals["extra_relation"] += 1
            print(f"  EXTRA RELATION (оба конца — реальные объекты, связи не было в gold): "
                  f"{from_lid} {ext.relation_type} {to_lid}")

    for r in extraction.rejected:
        if "evidence_quote связи не найден" in r:
            totals["rejected_by_grounding"] += 1
            print(f"  REJECTED (grounding): {r[:120]}")
        elif r.startswith("связь"):
            totals["rejected_other_structural"] += 1
            print(f"  REJECTED (структурно, не grounding): {r[:120]}")
    print()

print("########## ИТОГО ПО ВСЕМ КЛАССИФИЦИРОВАННЫМ КЕЙСАМ ##########")
for k, v in totals.items():
    print(f"  {k}: {v}")
scoreable = totals["correct"] + totals["wrong_type"] + totals["wrong_endpoints"] + totals["missing"]
print(f"  (для сверки) scoreable gold-связей классифицировано: {scoreable}")
extra_total = totals["extra_relation"] + totals["extra_due_to_entity_mismatch"]
matched_total = totals["correct"] + totals["wrong_type"]
denom = matched_total + extra_total
precision = matched_total / denom if denom else 0.0
print(f"  (для сверки) relation_precision по формуле харнесса на этом подмножестве: "
      f"{matched_total}/{denom} = {precision:.3f}")
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
    echo "  rm $m (появилась во время диагностики)"
    sudo docker compose exec -T ollama ollama rm "$m" >/dev/null 2>&1 || true
  else
    echo "  оставляем $m (была до диагностики)"
  fi
done
echo "-- лимит памяти -> $ORIGINAL_MEM_LIMIT_HUMAN --"
sudo docker update --memory="$ORIGINAL_MEM_LIMIT_HUMAN" --memory-swap="$ORIGINAL_MEM_LIMIT_HUMAN" "$(OLLAMA_CID)"

if [ "$diag_rc" -ne 0 ]; then
  echo "::error::диагностика завершилась с кодом $diag_rc"
  exit "$diag_rc"
fi
