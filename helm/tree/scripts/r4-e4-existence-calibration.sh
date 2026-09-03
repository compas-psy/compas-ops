#!/bin/bash
# HELM v4.0 RESCUE · R4.6.E шаг 4 (владелец 03.09.2026) — микро-
# calibration existence-only classifier (Pass 2A, `classify_existence()`)
# ДО полного 14-case relation subset: 15 positive + 15 hard-negative
# пар, построенных из УЖЕ существующих golden fixtures (никаких новых
# фактов/fixtures) — тем же методом, что и offline candidate audit
# (`r4-offline-candidate-audit.py`, R4.6.E шаг 1): `generate_candidates()`
# запущен на GOLD entities/atoms (не на шумном pass 1) и размечен против
# gold-рёбер. positive — все 15 candidate-пар, реально совпадающих с
# gold-ребром (ровно 15 доступно). hard-negative — 15 ложных candidate-
# пар, отобранных по приоритету критерия близости (overlap > mention >
# same_sentence > same_paragraph) — то есть максимально «похожих на
# связь» пар, которые ею НЕ являются: это и есть «hard», не случайная
# далёкая пара.
#
# Тестируются qwen2.5:7b (reference baseline C2/R4.6.C2) и mistral:7b
# (единственный из R4.6.D кандидатов с entity/atom-слоем не хуже
# qwen2.5:7b) — оба warm keep_alive=5m. Ни один pass1-вызов не нужен:
# объекты уже даны golden fixture, только 30 существование-вызовов на
# модель.
#
# Метрики (владелец): precision, recall, specificity, false-positive
# rate, NONE/false rate. Go/no-go (владелец, шаг 5) решается ПОСЛЕ
# прогона, не встроен сюда числовым порогом — «различает» или
# «систематически entailed=true» видно из самой confusion-матрицы.
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
echo "=== ollama pull mistral:7b ==="
sudo docker compose exec -T ollama ollama pull mistral:7b

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
import re
import time

from helm_core.knowledge.relation_classifier import classify_existence
from helm_core.knowledge.relation_candidates import generate_candidates
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_extract import ExtractedAtom, ExtractedEntity

_WS = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _locatable(quote: str, window_norm: str) -> bool:
    return bool(quote) and _normalize_ws(quote) in window_norm


SKIP = {"long_dense_window"}
REASON_PRIORITY = ["overlap", "mention", "same_sentence", "same_paragraph", "adjacent_sentence"]

cases = [c for c in GOLDEN_CASES if c.edges and c.case_id not in SKIP]

positive = []
negative_by_reason = {r: [] for r in REASON_PRIORITY}

for case in cases:
    window_norm = _normalize_ws(case.text)
    objects_by_ref = {}

    gold_entities = []
    for e in case.entities:
        quote = e.label if _locatable(e.label, window_norm) else next(
            (a for a in e.aliases if _locatable(a, window_norm)), "")
        if not quote:
            continue
        obj = ExtractedEntity(local_id=e.ref, entity_type=e.entity_type, label=e.label,
                              aliases=e.aliases, evidence_quote=quote)
        gold_entities.append(obj)
        objects_by_ref[e.ref] = obj

    gold_atoms = []
    for a in case.atoms:
        if not _locatable(a.canonical_text, window_norm):
            continue
        obj = ExtractedAtom(local_id=a.ref, kind=a.kind, title="", text=a.canonical_text,
                            evidence_quote=a.canonical_text)
        gold_atoms.append(obj)
        objects_by_ref[a.ref] = obj

    candidates = generate_candidates(gold_entities, gold_atoms, case.text)
    gold_pairs = {frozenset((edge.from_ref, edge.to_ref)) for edge in case.edges}

    for cand in candidates:
        pair = frozenset((cand.from_id, cand.to_id))
        from_obj = objects_by_ref[cand.from_id]
        to_obj = objects_by_ref[cand.to_id]
        row = (cand, from_obj, to_obj, case.case_id)
        if pair in gold_pairs:
            positive.append(row)
        else:
            negative_by_reason[cand.reason].append(row)

hard_negative = []
for r in REASON_PRIORITY:
    hard_negative.extend(negative_by_reason[r])
hard_negative = hard_negative[:15]

print(f"positive доступно: {len(positive)} (нужно 15)")
print(f"hard_negative отобрано: {len(hard_negative)} (нужно 15, доступно "
      f"{sum(len(v) for v in negative_by_reason.values())})")
assert len(positive) == 15, f"ожидалось ровно 15 positive из golden, получено {len(positive)}"
assert len(hard_negative) == 15, f"ожидалось 15 hard-negative, получено {len(hard_negative)}"

dataset = [(c, f, t, cid, True) for c, f, t, cid in positive] + \
          [(c, f, t, cid, False) for c, f, t, cid in hard_negative]


def evaluate(model: str, keep_alive: str) -> dict:
    tp = fp = tn = fn = 0
    reject_count = 0
    rows = []
    t0 = time.monotonic()
    for cand, from_obj, to_obj, case_id, is_positive in dataset:
        entailed, _evidence_quote, reject_reason = classify_existence(
            cand, from_obj=from_obj, to_obj=to_obj, model=model, keep_alive=keep_alive)
        if reject_reason is not None:
            reject_count += 1
        if is_positive and entailed:
            tp += 1
        elif is_positive and not entailed:
            fn += 1
        elif not is_positive and entailed:
            fp += 1
        else:
            tn += 1
        rows.append((case_id, cand.reason, is_positive, entailed, reject_reason))
    dt = time.monotonic() - t0

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    specificity = tn / (tn + fp) if (tn + fp) else float("nan")
    fpr = fp / (fp + tn) if (fp + tn) else float("nan")
    none_false_rate = (fn + tn) / len(dataset)

    print()
    print(f"########## {model} (keep_alive={keep_alive!r}) — existence calibration (Pass 2A only) ##########")
    print(f"  wall-clock: {dt:.1f}с, вызовов: {len(dataset)}, отказов разбора/grounding "
          f"(reject_reason не None): {reject_count}")
    print(f"  TP={tp} FP={fp} TN={tn} FN={fn}")
    print(f"  precision={precision:.3f} recall={recall:.3f} specificity={specificity:.3f} FPR={fpr:.3f}")
    print(f"  NONE/false rate (доля entailed=false по всем {len(dataset)}): {none_false_rate:.3f}")
    for case_id, reason, is_pos, entailed, reject_reason in rows:
        label = "POS" if is_pos else "NEG"
        pred = "entailed" if entailed else "none"
        mark = "OK" if (is_pos == entailed) else "MISS"
        extra = f" — {reject_reason}" if reject_reason else ""
        print(f"    [{mark}] {case_id:35s} {reason:16s} gold={label} pred={pred}{extra}")

    return {"precision": precision, "recall": recall, "specificity": specificity, "fpr": fpr,
            "none_false_rate": none_false_rate, "tp": tp, "fp": fp, "tn": tn, "fn": fn}


evaluate("qwen2.5:7b", "5m")
evaluate("mistral:7b", "5m")

print()
print("Go/no-go (владелец, R4.6.E шаг 5) решается по этим числам вручную:")
print("  различает positive/hard-negative -> C3 на 14-case subset;")
print("  обе модели систематически entailed=true (высокий FPR, низкая specificity)")
print("  -> chat-LLM relation-existence STOP, следующий шаг — НЕ ещё одна LLM.")
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
  echo "::error::калибровка завершилась с кодом $diag_rc"
  exit "$diag_rc"
fi
