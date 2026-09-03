#!/bin/bash
# HELM v4.0 RESCUE · R4.6.F1.2 (владелец 03.09.2026) — NLI relation
# benchmark на ЗАМОРОЖЕННОМ v3 dataset (`relation_benchmark_v3_fixtures.py`,
# freeze commit f8e32a576297d04c90b3bfb4fd2fdf7f1d1c4eb7): 95 positives +
# 190 negatives, 20 hand-written кейсов, quoted-reference verbalizer v3
# (не родовая ссылка v2, не canonical_text-как-именная-группа v1).
#
# Отличие методологии от R4.6.F1d (`r4-f1-nli-benchmark.sh`, LOOCV по
# всем 15 golden-кейсам сразу): здесь есть ЯВНЫЙ frozen split по `case_id`
# — 16 calibration-кейсов (78 positives) / 4 final_holdout-кейса (17
# positives). Порядок:
#   1. LOOCV ВНУТРИ calibration (16 фолдов) — санity-check, что порог
#      вообще стабильно достижим на calibration-данных.
#   2. Один финальный threshold, подобранный на ВСЕХ calibration-примерах
#      разом (max recall при precision >= 0.90).
#   3. Этот порог применяется РОВНО ОДИН РАЗ к final_holdout — это и есть
#      отчётные метрики продуктового гейта. Holdout НЕ участвует ни в
#      LOOCV, ни в подборе финального порога.
#   4. AUROC/AUPRC — отдельно на calibration и на final_holdout
#      (threshold-независимые, но holdout-версия — честная оценка
#      generalization, calibration-версия — informational).
#
# Тот же lifecycle-контракт, что во всех предыдущих R4.6 диагностических
# скриптах: set -Eeuo pipefail, ORIGINAL_MEMORY/MEMORY_SWAP раздельно,
# idempotent cleanup() через trap EXIT/INT/TERM, PRE/POST verification,
# sha256 исполняемого файла.
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

from helm_core.knowledge.nli_relation_dataset_v3 import build_examples_v3

examples = build_examples_v3()
calib = [e for e in examples if e.split == "calibration"]
holdout = [e for e in examples if e.split == "final_holdout"]
calib_case_ids = sorted({e.case_id for e in calib})

print(f"R4.6.F1.2 v3 dataset: {len(examples)} примеров всего "
      f"(freeze commit f8e32a576297d04c90b3bfb4fd2fdf7f1d1c4eb7)")
print(f"  calibration: {len(calib)} примеров, {len(calib_case_ids)} кейсов, "
      f"positive={sum(e.entailed for e in calib)}")
print(f"  final_holdout: {len(holdout)} примеров, "
      f"{len(set(e.case_id for e in holdout))} кейсов, "
      f"positive={sum(e.entailed for e in holdout)}")


def score(model_name: str, exs) -> tuple[list[float], dict]:
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
        for ex in exs:
            inputs = tokenizer(ex.premise, ex.hypothesis, return_tensors="pt", truncation=True)
            logits = model(**inputs).logits[0]
            p = torch.softmax(logits, dim=-1)[entail_idx].item()
            probs.append(p)
    infer_s = time.monotonic() - t0
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    stats = {
        "id2label": dict(id2label), "load_s": load_s, "infer_s": infer_s,
        "throughput": len(exs) / infer_s if infer_s else float("nan"),
        "peak_rss_mb": peak_rss_mb, "num_parameters": model.num_parameters(),
    }
    del model, tokenizer
    return probs, stats


def best_threshold(subset_examples, subset_probs, min_precision=0.90):
    """Максимизирует recall при precision >= 0.90 (product gate).
    `None`, если недостижимо на этом наборе — честно, не подменяется."""
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


def confusion(exs, probs, threshold) -> dict:
    tp = fp = tn = fn = 0
    for e, p in zip(exs, probs):
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
            "f1": f1, "specificity": specificity, "fpr": fpr}


def loocv_within_calibration(calib_exs, calib_probs) -> dict:
    tp = fp = tn = fn = 0
    fold_thresholds = []
    unreachable_folds = []
    for held_out in calib_case_ids:
        cal_e, cal_p, ho_e, ho_p = [], [], [], []
        for e, p in zip(calib_exs, calib_probs):
            (ho_e if e.case_id == held_out else cal_e).append(e)
            (ho_p if e.case_id == held_out else cal_p).append(p)
        threshold = best_threshold(cal_e, cal_p)
        if threshold is None:
            unreachable_folds.append(held_out)
            threshold = max(cal_p) + 1.0
        fold_thresholds.append((held_out, threshold))
        fold_result = confusion(ho_e, ho_p, threshold)
        tp += fold_result["tp"]; fp += fold_result["fp"]
        tn += fold_result["tn"]; fn += fold_result["fn"]
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else float("nan")
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall,
            "f1": f1, "fold_thresholds": fold_thresholds, "unreachable_folds": unreachable_folds}


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

for model_name in CANDIDATES:
    print(f"\n########## {model_name} ##########")
    all_probs, stats = score(model_name, examples)
    calib_probs = all_probs[:len(calib)]
    holdout_probs = all_probs[len(calib):]
    assert len(calib_probs) == len(calib) and len(holdout_probs) == len(holdout)

    print(f"  id2label: {stats['id2label']}, num_parameters: {stats['num_parameters']:,}")
    print(f"  load_time={stats['load_s']:.1f}с inference_time={stats['infer_s']:.1f}с "
          f"throughput={stats['throughput']:.1f} пар/с peak_rss={stats['peak_rss_mb']:.0f}MB "
          "(peak_rss — нарастающим итогом с начала процесса)")

    loocv_result = loocv_within_calibration(calib, calib_probs)
    print("  ---- шаг 1: LOOCV внутри calibration (16 фолдов) — sanity-check ----")
    print(f"  TP={loocv_result['tp']} FP={loocv_result['fp']} TN={loocv_result['tn']} FN={loocv_result['fn']}")
    print(f"  precision={loocv_result['precision']:.3f} recall={loocv_result['recall']:.3f} "
          f"F1={loocv_result['f1']:.3f}")
    if loocv_result["unreachable_folds"]:
        print(f"  ВНИМАНИЕ: gate precision>=0.90 недостижим на calibration для фолдов: "
              f"{loocv_result['unreachable_folds']}")

    final_threshold = best_threshold(calib, calib_probs)
    print(f"\n  ---- шаг 2: финальный threshold на ВСЕХ {len(calib)} calibration-примерах ----")
    if final_threshold is None:
        print("  ВНИМАНИЕ: precision>=0.90 недостижим ни на одном threshold на всём calibration set")
        final_threshold = max(calib_probs) + 1.0
    else:
        print(f"  final_threshold={final_threshold:.4f} (NLI probability, НЕ путать с product gate 0.90)")

    print(f"\n  ---- шаг 3: ОДНОКРАТНОЕ применение final_threshold к final_holdout "
          f"({len(holdout)} примеров, {len(set(e.case_id for e in holdout))} кейсов) — ОТЧЁТНЫЙ РЕЗУЛЬТАТ ----")
    holdout_result = confusion(holdout, holdout_probs, final_threshold)
    print(f"  TP={holdout_result['tp']} FP={holdout_result['fp']} "
          f"TN={holdout_result['tn']} FN={holdout_result['fn']}")
    print(f"  typed relation precision (final_holdout)={holdout_result['precision']:.3f} "
          f"recall={holdout_result['recall']:.3f} F1={holdout_result['f1']:.3f} "
          f"specificity={holdout_result['specificity']:.3f} FPR={holdout_result['fpr']:.3f}")

    calib_labels = [e.entailed for e in calib]
    holdout_labels = [e.entailed for e in holdout]
    print(f"\n  AUROC calibration={auroc(calib_labels, calib_probs):.3f} "
          f"AUPRC calibration={average_precision(calib_labels, calib_probs):.3f}")
    print(f"  AUROC final_holdout={auroc(holdout_labels, holdout_probs):.3f} "
          f"AUPRC final_holdout={average_precision(holdout_labels, holdout_probs):.3f}")

    precision_is_nan = holdout_result["precision"] != holdout_result["precision"]  # NaN != NaN
    gate_pass = (not precision_is_nan) and holdout_result["precision"] >= 0.90
    print(f"\n  GATE (typed precision>=0.90 на final_holdout, без коллапса recall): "
          f"{'PASS' if gate_pass else 'FAIL'} (precision={holdout_result['precision']:.3f}, "
          f"recall={holdout_result['recall']:.3f})")

print("\nПродуктовый гейт (владелец): typed relation precision >= 0.90 на final_holdout "
      "БЕЗ коллапса recall, порог подобран ТОЛЬКО на calibration. "
      "Go/no-go к R4.6.F2 — решение владельца по этим цифрам.")
PYEOF

if [ "$diag_rc" -ne 0 ]; then
  echo "::error::бенчмарк завершился с кодом $diag_rc"
fi
exit "$diag_rc"
