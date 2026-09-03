#!/bin/bash
# HELM v4.0 RESCUE · R4.6.F1d (владелец 03.09.2026) — полный NLI
# relation-existence/typing benchmark: MoritzLaurer/mDeBERTa-v3-base-
# xnli-multilingual-nli-2mil7 и cointegrated/rubert-base-cased-nli-
# threeway на детерминированном датасете `nli_relation_dataset.py`
# (R4.6.F1a/b — 141 typed+directed примеров из ВСЕХ 15 golden-кейсов с
# рёбрами, generate_candidates() не используется).
#
# Runtime подтверждён smoke-тестом (R4.6.F1c, run 232): helm-knowledge-
# worker уже несёт CPU-only torch + transformers (Docling), обе модели
# грузятся в пределах бюджета памяти, id2label ПРОЧИТАН эмпирически у
# каждой модели (не предполагается — mDeBERTa: entailment/neutral/
# contradiction по индексам 0/1/2; rubert: entailment/contradiction/
# neutral — ПОРЯДОК РАЗНЫЙ).
#
# Методология (владелец, обязательна): threshold НЕ подбирается на тех
# же примерах, на которых репортируется итог. Leave-one-case-out по
# `case_id`: на 14 из 15 кейсов подбирается threshold, максимизирующий
# recall при precision >= 0.90 (сам product gate), применяется к
# оставшемуся held-out кейсу; агрегат — по всем 15 фолдам. AUROC/AUPRC
# — threshold-независимые, по всему датасету сразу.
#
# Тот же lifecycle-контракт, что r4-e4-existence-calibration.sh и
# r4-f1-nli-smoke-test.sh: set -Eeuo pipefail, ORIGINAL_MEMORY/
# MEMORY_SWAP раздельно, idempotent cleanup() через trap EXIT/INT/TERM,
# PRE/POST verification, sha256 исполняемого файла.
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
sudo docker inspect -f '{{.State.Status}}' "$CID"
sudo docker inspect -f '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$CID"
sudo docker stats --no-stream "$CID"
df -h / | tail -1
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

  sudo docker update --memory="$ORIGINAL_MEMORY" --memory-swap="$ORIGINAL_MEMORY_SWAP" "$cid" >/dev/null 2>&1 \
    || echo "::error::не удалось восстановить memory limit — проверьте helm-knowledge-worker вручную"

  echo "=== POST: состояние helm-knowledge-worker после cleanup ==="
  sudo docker inspect -f '{{.State.Status}}' "$cid" || true
  post_memory=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$cid" 2>/dev/null || echo "?")
  post_swap=$(sudo docker inspect -f '{{.HostConfig.MemorySwap}}' "$cid" 2>/dev/null || echo "?")
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
import resource
import time

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from helm_core.knowledge.nli_relation_dataset import build_examples

examples = build_examples()
n = len(examples)
case_ids = sorted({e.case_id for e in examples})
print(f"датасет R4.6.F1: {n} примеров, {len(case_ids)} кейсов (offline, детерминированно, без Ollama)")
print(f"positive={sum(e.entailed for e in examples)} "
      f"hard_negative={sum(not e.entailed for e in examples)}")


def score_model(model_name: str) -> tuple[list[float], dict]:
    t0 = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    model.eval()
    load_s = time.monotonic() - t0
    id2label = model.config.id2label
    entail_idx = next(i for i, label in id2label.items() if label.lower().startswith("entail"))

    probs: list[float] = []
    t0 = time.monotonic()
    with torch.no_grad():
        for ex in examples:
            inputs = tokenizer(ex.premise, ex.hypothesis, return_tensors="pt", truncation=True)
            logits = model(**inputs).logits[0]
            p = torch.softmax(logits, dim=-1)[entail_idx].item()
            probs.append(p)
    infer_s = time.monotonic() - t0
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    stats = {
        "id2label": dict(id2label), "load_s": load_s, "infer_s": infer_s,
        "throughput": n / infer_s if infer_s else float("nan"),
        "peak_rss_mb": peak_rss_mb, "num_parameters": model.num_parameters(),
    }
    del model, tokenizer
    return probs, stats


def best_threshold(subset_examples, subset_probs, min_precision=0.90):
    """Владелец: подбирается на CALIBRATION-фолде — максимизирует recall
    при precision >= 0.90 (сам product gate, НЕ произвольная эвристика).
    `None`, если ни один порог на этом фолде не достигает precision —
    честно, не подменяется произвольным значением."""
    candidates = sorted(set(subset_probs))
    best = None
    for t in candidates:
        tp = sum(1 for e, p in zip(subset_examples, subset_probs) if p >= t and e.entailed)
        fp = sum(1 for e, p in zip(subset_examples, subset_probs) if p >= t and not e.entailed)
        fn = sum(1 for e, p in zip(subset_examples, subset_probs) if p < t and e.entailed)
        if tp + fp == 0:
            continue
        precision = tp / (tp + fp)
        if precision >= min_precision:
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            if best is None or recall > best[1]:
                best = (t, recall)
    return best[0] if best else None


def loocv(probs: list[float]) -> dict:
    tp = fp = tn = fn = 0
    fold_thresholds = []
    unreachable_folds = []
    for held_out in case_ids:
        cal_examples, cal_probs, ho_examples, ho_probs = [], [], [], []
        for e, p in zip(examples, probs):
            if e.case_id == held_out:
                ho_examples.append(e)
                ho_probs.append(p)
            else:
                cal_examples.append(e)
                cal_probs.append(p)
        threshold = best_threshold(cal_examples, cal_probs)
        if threshold is None:
            unreachable_folds.append(held_out)
            threshold = max(cal_probs) + 1.0  # безопасный fallback: никогда не сработает -> "не entailed"
        fold_thresholds.append((held_out, threshold))
        for e, p in zip(ho_examples, ho_probs):
            predicted = p >= threshold
            if e.entailed and predicted:
                tp += 1
            elif e.entailed and not predicted:
                fn += 1
            elif not e.entailed and predicted:
                fp += 1
            else:
                tn += 1

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else float("nan")
    specificity = tn / (tn + fp) if (tn + fp) else float("nan")
    fpr = fp / (fp + tn) if (fp + tn) else float("nan")
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall,
            "f1": f1, "specificity": specificity, "fpr": fpr,
            "fold_thresholds": fold_thresholds, "unreachable_folds": unreachable_folds}


def auroc(labels: list[bool], scores: list[float]) -> float:
    paired = sorted(zip(scores, labels))
    total = len(paired)
    ranks = [0.0] * total
    i = 0
    while i < total:
        j = i
        while j + 1 < total and paired[j + 1][0] == paired[i][0]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    n_pos = sum(1 for _, l in paired if l)
    n_neg = total - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    sum_ranks_pos = sum(r for r, (_, l) in zip(ranks, paired) if l)
    return (sum_ranks_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def average_precision(labels: list[bool], scores: list[float]) -> float:
    order = sorted(range(len(scores)), key=lambda idx: -scores[idx])
    n_pos = sum(labels)
    if n_pos == 0:
        return float("nan")
    tp = fp = 0
    ap = 0.0
    prev_recall = 0.0
    for idx in order:
        if labels[idx]:
            tp += 1
        else:
            fp += 1
        precision = tp / (tp + fp)
        recall = tp / n_pos
        ap += precision * (recall - prev_recall)
        prev_recall = recall
    return ap


CANDIDATES = [
    "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7",
    "cointegrated/rubert-base-cased-nli-threeway",
]

labels = [e.entailed for e in examples]

for model_name in CANDIDATES:
    print(f"\n########## {model_name} ##########")
    probs, stats = score_model(model_name)
    print(f"  id2label: {stats['id2label']}, num_parameters: {stats['num_parameters']:,}")
    print(f"  load_time={stats['load_s']:.1f}с inference_time={stats['infer_s']:.1f}с "
          f"throughput={stats['throughput']:.1f} пар/с peak_rss={stats['peak_rss_mb']:.0f}MB "
          f"(peak_rss — нарастающим итогом с начала процесса, не изолированно на эту модель)")

    result = loocv(probs)
    print(f"  ---- leave-one-case-out ({len(case_ids)} фолдов), product gate typed precision >= 0.90 "
          f"на КАЖДОМ calibration-фолде, применено к held-out ----")
    print(f"  TP={result['tp']} FP={result['fp']} TN={result['tn']} FN={result['fn']}")
    print(f"  typed relation precision (агрегат по held-out)={result['precision']:.3f} "
          f"recall={result['recall']:.3f} F1={result['f1']:.3f} "
          f"specificity={result['specificity']:.3f} FPR={result['fpr']:.3f}")
    if result["unreachable_folds"]:
        print(f"  ВНИМАНИЕ: gate precision>=0.90 недостижим на calibration-данных для фолдов: "
              f"{result['unreachable_folds']} (fallback — held-out кейс всегда предсказан 'не entailed')")
    print("  пороги (NLI probability threshold) по фолдам — НЕ путать с product gate 0.90:")
    for held_out, threshold in result["fold_thresholds"]:
        print(f"    held-out={held_out:35s} calibration_threshold={threshold:.4f}")

    print(f"  AUROC (весь датасет, threshold-независимо)={auroc(labels, probs):.3f}")
    print(f"  AUPRC/average_precision (весь датасет)={average_precision(labels, probs):.3f}")

print("\nПродуктовый гейт (владелец): typed relation precision >= 0.90 на held-out БЕЗ коллапса recall.")
print("Числа выше — по каждой модели отдельно; go/no-go к R4.6.F2 — решение владельца по этим цифрам.")
PYEOF

if [ "$diag_rc" -ne 0 ]; then
  echo "::error::бенчмарк завершился с кодом $diag_rc"
fi
exit "$diag_rc"
