#!/bin/bash
# HELM v4.0 RESCUE · R4 — read-only разбор причины, по которой
# run_golden_canonical() для gemma2:2b и qwen2.5:3b (run 196) не дошёл
# до валидного result.json, хотя run_candidate() продолжил работу дальше
# (script не проверяет код возврата run_golden_canonical). Печатает
# stderr.log и hвост result.json.tmp для каждого незавершённого
# кандидата, плюс отдельный вызов validate для точной причины отказа.
set -uo pipefail
BASE_DIR=/opt/helm-state/benchmarks/r4
cd /opt/helm/compose

for d in "$BASE_DIR"/*/; do
  name=$(basename "$d")
  [ "$name" = "run1" ] && continue
  if [ -s "$d/result.json" ]; then
    echo "=== $name: result.json ЕСТЬ (валиден) — пропускаю ==="
    continue
  fi
  if [ ! -s "$d/result.json.tmp" ]; then
    echo "=== $name: ни result.json, ни result.json.tmp — пропускаю ==="
    continue
  fi
  echo "############ $name: НЕЗАВЕРШЁННЫЙ КАНДИДАТ ############"
  echo "-- fingerprint.json --"
  sudo cat "$d/fingerprint.json" 2>/dev/null
  echo
  echo "-- stderr.log (последние 60 строк) --"
  sudo tail -60 "$d/stderr.log" 2>/dev/null
  echo
  echo "-- result.json.tmp: размер + первые/последние 20 строк --"
  sudo wc -l "$d/result.json.tmp" 2>/dev/null
  echo "... первые 20:"
  sudo head -20 "$d/result.json.tmp" 2>/dev/null
  echo "... последние 20:"
  sudo tail -20 "$d/result.json.tmp" 2>/dev/null
  echo
  echo "-- validate (точная причина отказа) --"
  model=$(sudo python3 -c "import json; print(json.load(open('$d/fingerprint.json'))['model_tag'])" 2>/dev/null)
  fp_hash=$(sudo python3 -c "import json; print(json.load(open('$d/fingerprint.json'))['fingerprint_hash'])" 2>/dev/null)
  sudo docker compose exec -T helm-core \
    python3 -m helm_core.knowledge.semantic_benchmark validate \
    --file /dev/stdin --expect-model "$model" --expect-fingerprint-hash "$fp_hash" \
    < "$d/result.json.tmp" 2>&1
  echo
done
