#!/bin/bash
# HELM v4.0 RESCUE · R4 root-cause analysis: read-only dump of the artifacts
# from the concluded (overall_pass=False) final acceptance run.
#
# Owner mandate 2026-09-04 (после R4 BLOCKED, run 241): "Разрешена только
# read-only диагностика сохранённых артефактов... без рестартов, БД-записей
# и inference." Этот скрипт не трогает Ollama/Postgres/контейнеры — только
# cat уже записанных JSON/логов под /opt/helm-state/benchmarks/r4-final-acceptance/.
set -uo pipefail

BASE_DIR=/opt/helm-state/benchmarks/r4-final-acceptance

echo "############ ЛИСТИНГ ############"
sudo find "$BASE_DIR" -type f -printf '%s\t%p\n' 2>/dev/null | sort -k2

RUN_DIR=$(sudo find "$BASE_DIR" -maxdepth 1 -type d -name 'qwen2_5_7b-*' | sort | tail -1)
echo
echo "############ RUN_DIR: $RUN_DIR ############"

for f in "$BASE_DIR/R4_FINAL_ACCEPTANCE.json" \
         "$BASE_DIR/resources-qwen2_5_7b.json" \
         "$RUN_DIR/fingerprint.json" \
         "$RUN_DIR/result.json" \
         "$RUN_DIR/stderr.log"; do
  echo
  echo "############ FILE: $f ############"
  if sudo test -f "$f"; then
    sudo cat "$f"
  else
    echo "(отсутствует)"
  fi
done

echo
echo "############ ГОТОВО ############"
