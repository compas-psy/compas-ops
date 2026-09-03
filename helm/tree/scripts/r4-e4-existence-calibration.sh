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
# Владелец 03.09.2026 (lifecycle-хардненинг после инцидента с run 225 —
# отменённый прогон оставил temporary memory limit и непреэкзистентную
# модель, пока не была написана read-only верификация вручную): -e
# обязателен (без него `docker update`/`ollama pull` мог упасть, а
# скрипт продолжал бы как ни в чём не бывало), -E — чтобы ERR тоже
# срабатывал внутри функций. cleanup() теперь идемпотентна и навешена
# через trap на EXIT/INT/TERM — восстановление гарантировано при ЛЮБОМ
# способе завершения (нормальном, ошибке, отмене прогона), а не только
# при штатном доходе до конца файла.
set -Eeuo pipefail
cd /opt/helm/compose

OLLAMA_CID() { sudo docker compose ps -q ollama; }

echo "=== sha256 исполняемого скрипта (доказательство: тот же байт-в-байт файл, что закоммичен) ==="
sha256sum "${BASH_SOURCE[0]:-$0}" || true

CID="$(OLLAMA_CID)"
if [ -z "$CID" ]; then
  echo "::error::контейнер ollama не найден — не продолжаем"
  exit 1
fi

echo
echo "=== PRE: состояние ollama до изменений ==="
echo "--- ollama list ---"
sudo docker compose exec -T ollama ollama list
echo "--- container state ---"
sudo docker inspect -f '{{.State.Status}}' "$CID"
echo "--- HostConfig.Memory / HostConfig.MemorySwap ---"
sudo docker inspect -f '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$CID"

# Владелец: сохранить Memory и MemorySwap ОТДЕЛЬНО (не предполагать, что
# они равны или что можно перевести в "человеческое" значение вроде
# "4g" — раньше пустой/нулевой Memory при восстановлении подменялся
# угаданной строкой; теперь восстанавливаем ровно то число, которое
# было, включая "0"/"-1" — Docker принимает их как есть).
PREEXISTING_MODELS=$(sudo docker compose exec -T ollama ollama list | tail -n +2 | awk '{print $1}')
ORIGINAL_MEMORY=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$CID")
ORIGINAL_MEMORY_SWAP=$(sudo docker inspect -f '{{.HostConfig.MemorySwap}}' "$CID")

# Перед изменением ресурсов — убедиться, что есть чем восстанавливать.
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
  local cid current_models m found p post_memory post_swap
  cid="$(OLLAMA_CID)"

  if [ -z "$cid" ]; then
    echo "::error::контейнер ollama не найден на этапе cleanup — восстановление невозможно"
    exit "$rc"
  fi

  current_models=$(sudo docker compose exec -T ollama ollama list 2>/dev/null | tail -n +2 | awk '{print $1}') || current_models=""
  for m in $current_models; do
    found=0
    for p in $PREEXISTING_MODELS; do
      [ "$m" = "$p" ] && found=1 && break
    done
    if [ "$found" -eq 0 ]; then
      sudo docker compose exec -T ollama ollama rm "$m" >/dev/null 2>&1 || true
    fi
  done

  sudo docker update --memory="$ORIGINAL_MEMORY" --memory-swap="$ORIGINAL_MEMORY_SWAP" "$cid" >/dev/null 2>&1 \
    || echo "::error::не удалось восстановить memory limit — проверьте контейнер ollama вручную"

  echo "=== POST: состояние ollama после cleanup ==="
  echo "--- ollama list ---"
  sudo docker compose exec -T ollama ollama list || true
  echo "--- container state ---"
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

  # $rc сохранён в самом начале функции — cleanup не имеет права
  # подменить код завершения скрипта своими собственными командами.
  exit "$rc"
}
trap cleanup EXIT INT TERM

echo
echo "=== временно поднимаем лимит ollama до 8g ==="
sudo docker update --memory=8g --memory-swap=8g "$CID"
echo "=== ollama pull qwen2.5:7b ==="
sudo docker compose exec -T ollama ollama pull qwen2.5:7b
echo "=== ollama pull mistral:7b ==="
sudo docker compose exec -T ollama ollama pull mistral:7b

# Не изменено ни на строку относительно закоммиченной ранее версии
# (владелец п.1) — dataset/prompts/архитектура 2A/2B здесь не трогаются,
# правится только lifecycle bash-обвязки вокруг.
diag_rc=0
sudo docker compose exec -T helm-core python3 - <<'PYEOF' || diag_rc=$?
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

if [ "$diag_rc" -ne 0 ]; then
  echo "::error::калибровка завершилась с кодом $diag_rc"
fi
# Восстановление (модели + memory limit + PRE/POST сверка) выполняет
# cleanup() через trap EXIT — сработает независимо от того, как именно
# скрипт сюда дошёл. Явный exit нужен только чтобы код завершения был
# ИМЕННО diag_rc, а не 0 от последнего "if".
exit "$diag_rc"
