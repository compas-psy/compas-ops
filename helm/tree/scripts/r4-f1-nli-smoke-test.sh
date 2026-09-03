#!/bin/bash
# HELM v4.0 RESCUE · R4.6.F1c (владелец 03.09.2026) — smoke-test для
# purpose-built local NLI relation scorer, ПЕРЕД полным F1-бенчмарком.
#
# Кандидаты: MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7,
# cointegrated/rubert-base-cased-nli-threeway. Local CPU, zero cloud.
#
# Выполняется внутри `helm-knowledge-worker` (НЕ helm-core: у helm-core
# лимит 768MB и нет torch вовсе; worker уже несёт CPU-only torch —
# Dockerfile.worker — и Docling, чья транзитивная зависимость почти
# наверняка уже тянет `transformers`/`safetensors` — нулевая/минимальная
# новая установка вместо полной torch+transformers с нуля).
#
# Цель ТОЛЬКО: (1) убедиться, что обе модели вообще скачиваются и
# грузятся в границах памяти этого контейнера; (2) прочитать РЕАЛЬНЫЙ
# `id2label` каждой модели (не предполагать порядок entailment/neutral/
# contradiction — он не гарантированно одинаков между моделями);
# (3) прогнать one-shot sanity-инференс на паре реальных примеров из
# `nli_relation_dataset.build_examples()` и посмотреть на вероятности;
# (4) измерить disk footprint кэша и peak RSS. Полный LOOCV-бенчмарк —
# отдельный скрипт (F1d), после этого smoke-теста.
#
# Тот же lifecycle-контракт, что и r4-e4-existence-calibration.sh
# (владелец 03.09.2026, после инцидента с run 225): set -Eeuo pipefail,
# ORIGINAL_MEMORY/MEMORY_SWAP сохранены раздельно, idempotent cleanup()
# через trap EXIT/INT/TERM, PRE/POST verification, sha256 исполняемого
# файла в начале лога.
set -Eeuo pipefail
cd /opt/helm/compose

WORKER_CID() { sudo docker compose ps -q helm-knowledge-worker; }

echo "=== sha256 исполняемого скрипта ==="
sha256sum "${BASH_SOURCE[0]:-$0}" || true

CID="$(WORKER_CID)"
if [ -z "$CID" ]; then
  echo "::error::контейнер helm-knowledge-worker не найден — не продолжаем"
  exit 1
fi

echo
echo "=== PRE: состояние helm-knowledge-worker до изменений ==="
echo "--- container state ---"
sudo docker inspect -f '{{.State.Status}}' "$CID"
echo "--- HostConfig.Memory / HostConfig.MemorySwap ---"
sudo docker inspect -f '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$CID"
echo "--- docker stats (текущее потребление) ---"
sudo docker stats --no-stream "$CID"
echo "--- диск на хосте ---"
df -h / | tail -1
echo "--- память на хосте ---"
free -h | head -2

ORIGINAL_MEMORY=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$CID")
ORIGINAL_MEMORY_SWAP=$(sudo docker inspect -f '{{.HostConfig.MemorySwap}}' "$CID")
if [ -z "$ORIGINAL_MEMORY" ] || [ -z "$ORIGINAL_MEMORY_SWAP" ]; then
  echo "::error::не удалось прочитать исходные HostConfig.Memory/MemorySwap — не продолжаем"
  exit 1
fi

CLEANUP_DONE=0
cleanup() {
  local rc=$?
  if [ "$CLEANUP_DONE" -eq 1 ]; then
    exit "$rc"
  fi
  CLEANUP_DONE=1

  echo
  echo "############ CLEANUP (idempotent; исходный код завершения: $rc) ############"
  local cid post_memory post_swap
  cid="$(WORKER_CID)"
  if [ -z "$cid" ]; then
    echo "::error::контейнер helm-knowledge-worker не найден на этапе cleanup"
    exit "$rc"
  fi

  # Кэш HF/pip-установки этого smoke-теста — ТОЛЬКО в домашнем каталоге
  # контейнера (эфемерный слой), ничего не смонтировано с хоста, поэтому
  # достаточно вернуть memory limit — сам контейнер не персистентен
  # относительно этого прогона (следующий force-recreate/деплой всё
  # равно стирает слой).
  sudo docker update --memory="$ORIGINAL_MEMORY" --memory-swap="$ORIGINAL_MEMORY_SWAP" "$cid" >/dev/null 2>&1 \
    || echo "::error::не удалось восстановить memory limit — проверьте helm-knowledge-worker вручную"

  echo "=== POST: состояние helm-knowledge-worker после cleanup ==="
  sudo docker inspect -f '{{.State.Status}}' "$cid" || true
  post_memory=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$cid" 2>/dev/null || echo "?")
  post_swap=$(sudo docker inspect -f '{{.HostConfig.MemorySwap}}' "$cid" 2>/dev/null || echo "?")
  echo "--- HostConfig.Memory / HostConfig.MemorySwap ---"
  echo "$post_memory $post_swap"
  if [ "$post_memory" = "$ORIGINAL_MEMORY" ] && [ "$post_swap" = "$ORIGINAL_MEMORY_SWAP" ]; then
    echo "POST совпадает с PRE: Memory=$ORIGINAL_MEMORY MemorySwap=$ORIGINAL_MEMORY_SWAP"
  else
    echo "::error::POST НЕ совпадает с PRE — было Memory=$ORIGINAL_MEMORY/MemorySwap=$ORIGINAL_MEMORY_SWAP, стало Memory=$post_memory/MemorySwap=$post_swap"
  fi

  exit "$rc"
}
trap cleanup EXIT INT TERM

echo
echo "=== временно поднимаем лимит helm-knowledge-worker до 6g (постоянный лимит: 3g) ==="
sudo docker update --memory=6g --memory-swap=6g "$CID"

diag_rc=0
sudo docker compose exec -T helm-knowledge-worker python3 - <<'PYEOF' || diag_rc=$?
import gc
import resource
import time

print("=== проверка наличия transformers/safetensors (уже тянутся Docling?) ===")
try:
    import transformers
    print(f"transformers уже установлен: {transformers.__version__}")
except ImportError:
    print("transformers НЕ установлен — устанавливаем (torch уже есть, доустановка лёгкая)")
    import subprocess
    import sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "--no-cache-dir",
                           "transformers", "safetensors"])
    import transformers
    print(f"transformers установлен: {transformers.__version__}")

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from helm_core.knowledge.nli_relation_dataset import build_examples

examples = build_examples()
print(f"\nдатасет R4.6.F1: {len(examples)} примеров (offline, детерминированно, без Ollama)")
sample = [e for e in examples if e.case_id == "organization_fact"][:4]

CANDIDATES = [
    "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7",
    "cointegrated/rubert-base-cased-nli-threeway",
]

for model_name in CANDIDATES:
    print(f"\n########## {model_name} ##########")
    t0 = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    model.eval()
    load_s = time.monotonic() - t0
    print(f"  load time: {load_s:.1f}с")
    print(f"  id2label (РЕАЛЬНЫЙ, не предполагается): {model.config.id2label}")
    print(f"  num_parameters: {model.num_parameters():,}")

    t0 = time.monotonic()
    with torch.no_grad():
        for ex in sample:
            inputs = tokenizer(ex.premise, ex.hypothesis, return_tensors="pt", truncation=True)
            logits = model(**inputs).logits[0]
            probs = torch.softmax(logits, dim=-1)
            label_probs = {model.config.id2label[i]: round(p.item(), 4) for i, p in enumerate(probs)}
            print(f"    [{ex.kind:18s} gold_entailed={ex.entailed!s:5s}] {label_probs}")
    infer_s = time.monotonic() - t0
    print(f"  inference: {len(sample)} пар за {infer_s:.2f}с ({infer_s / len(sample):.3f}с/пара)")

    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"  peak RSS процесса (нарастающим итогом с начала): {peak_rss_mb:.0f} MB")

    del model, tokenizer
    gc.collect()

print("\nsmoke-test завершён без исключений — обе модели грузятся и отвечают "
      "в границах памяти этого прогона.")
PYEOF

echo
echo "=== disk footprint HF-кэша внутри контейнера ==="
sudo docker compose exec -T helm-knowledge-worker sh -c 'du -sh ~/.cache/huggingface 2>/dev/null || echo "кэш не найден"'

if [ "$diag_rc" -ne 0 ]; then
  echo "::error::smoke-test завершился с кодом $diag_rc"
fi
exit "$diag_rc"
