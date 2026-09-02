#!/bin/bash
# HELM v4.0 RESCUE · R4 — печатает JSON-отчёты кандидатов в лог recon,
# чтобы выбор winner (semantic_benchmark_selection.select_winner) можно
# было прогнать вне сервера на уже полученных данных, не гоняя Ollama
# ещё раз ради самого выбора. Read-only: только cat.
set -uo pipefail
RUN_DIR=/opt/helm-state/benchmarks/r4/run1

for f in "$RUN_DIR"/golden-*.json; do
  [ -e "$f" ] || continue
  echo "############ BEGIN $(basename "$f") ############"
  sudo cat "$f"
  echo
  echo "############ END $(basename "$f") ############"
done

for f in "$RUN_DIR"/keepalive-*.log; do
  [ -e "$f" ] || continue
  echo "############ BEGIN $(basename "$f") ############"
  sudo cat "$f"
  echo "############ END $(basename "$f") ############"
done

echo "############ ФАЙЛЫ В КАТАЛОГЕ ############"
sudo ls -la "$RUN_DIR"
