#!/bin/bash
# HELM v4.0 RESCUE · R4 — печатает JSON-отчёты кандидатов в лог recon,
# чтобы выбор winner (semantic_benchmark_selection.select_winner) можно
# было прогнать вне сервера на уже полученных данных, не гоняя Ollama
# ещё раз ради самого выбора. Read-only: только cat/find.
#
# Каталоги канонических результатов теперь именуются по fingerprint
# (r4-golden-benchmark.sh, ретракция владельца п.4) — не по фиксированному
# "run1", поэтому здесь ищем по маске, а не по жёстко зашитым путям.
set -uo pipefail
BASE_DIR=/opt/helm-state/benchmarks/r4

echo "############ КАТАЛОГ ЦЕЛИКОМ ############"
sudo find "$BASE_DIR" -maxdepth 3 2>/dev/null | sort

for f in "$BASE_DIR"/*/result.json; do
  [ -e "$f" ] || continue
  echo "############ BEGIN $f ############"
  sudo cat "$f"
  echo
  echo "############ END $f ############"
done

for f in "$BASE_DIR"/*/fingerprint.json; do
  [ -e "$f" ] || continue
  echo "############ BEGIN $f ############"
  sudo cat "$f"
  echo
  echo "############ END $f ############"
done

for f in "$BASE_DIR"/resources-*.json; do
  [ -e "$f" ] || continue
  echo "############ BEGIN $(basename "$f") ############"
  sudo cat "$f"
  echo
  echo "############ END $(basename "$f") ############"
done

for f in "$BASE_DIR"/keepalive-*.log; do
  [ -e "$f" ] || continue
  echo "############ BEGIN $(basename "$f") ############"
  sudo cat "$f"
  echo "############ END $(basename "$f") ############"
done
