#!/bin/bash
# HELM v4.0 RESCUE · R4.5.5 — read-only recon: печатает сырые result.json
# трёх новых кандидатов (run 210, новая fingerprint-директория после
# R4.5.2/R4.5.3) и трёх старых (run 200, для recall-before сравнения по
# распоряжению владельца 03.09.2026), плюс resources-*.json новых
# кандидатов. Ничего не меняет, ничего не запускает — только cat.
set -uo pipefail

BASE_DIR=/opt/helm-state/benchmarks/r4

echo "############ ЛИСТИНГ $BASE_DIR ############"
sudo find "$BASE_DIR" -maxdepth 1 -type d -name '*-*' | sort

for model_prefix in gemma2_2b qwen2_5_3b qwen2_5_7b; do
  echo
  echo "############ ВСЕ result.json для $model_prefix ############"
  sudo find "$BASE_DIR" -maxdepth 2 -path "*/${model_prefix}-*/result.json" | sort
done

NEW_DIRS=(
  "gemma2_2b-bf9d1c7abb31112d"
  "qwen2_5_3b-b1028b51172d67eb"
  "qwen2_5_7b-bae0de6a6f9e333b"
)

for d in "${NEW_DIRS[@]}"; do
  echo
  echo "############ RUN210 result.json: $d ############"
  sudo cat "$BASE_DIR/$d/result.json"
done

echo
echo "############ RUN210 resources-*.json ############"
for f in resources-gemma2_2b.json resources-qwen2_5_3b.json resources-qwen2_5_7b.json; do
  echo "--- $f ---"
  sudo cat "$BASE_DIR/$f"
done

echo
echo "############ Старые (run 200) result.json — все директории, КРОМЕ трёх новых выше ############"
for model_prefix in gemma2_2b qwen2_5_3b qwen2_5_7b; do
  for d in $(sudo find "$BASE_DIR" -maxdepth 1 -type d -name "${model_prefix}-*" -printf '%f\n' | sort); do
    skip=0
    for nd in "${NEW_DIRS[@]}"; do
      [ "$d" = "$nd" ] && skip=1
    done
    if [ "$skip" -eq 0 ]; then
      echo
      echo "############ OLD result.json: $d ############"
      sudo cat "$BASE_DIR/$d/result.json"
    fi
  done
done
