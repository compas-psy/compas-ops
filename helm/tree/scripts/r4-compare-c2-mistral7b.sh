#!/bin/bash
# HELM v4.0 RESCUE · R4.6.D candidate 3/3 — mistral:7b (Mistral AI,
# классическая сильная instruction-tuned линейка, отдельный vendor; тот
# же size-class, что qwen2.5:7b — прямое сравнение архитектур на одном
# resource envelope).
#
# Владелец 03.09.2026 (после run 222): C2/per-pair benchmark'и run
# 217-224 ВСЕ гоняли keep_alive=0 — production policy для single-pass
# (один вызов на окно), но НЕ оправдана для C2 (N+1 вызовов на кейс:
# pass 1 + один classify_relation() на каждого кандидата) — каждый
# вызов платил полный cold-load, раздувая wall-clock в разы против
# того, что дал бы разумный деплой (модель тёплая между вызовами ОДНОГО
# кейса). Зафиксировано как benchmark implementation debt (не quality
# issue — qwen2.5:7b/llama3.2:3b/phi3.5 уже провалили C2 по качеству
# независимо от скорости, их не перегоняем: KNOWLEDGE_MODELS.md
# R4.6.C2/R4.6.D). Для mistral:7b (первый непроверенный кандидат ПОСЛЕ
# этого распоряжения) — bounded warm lifecycle: keep_alive=5m,
# resident между pass 1 и всеми classify_relation() ОДНОГО кейса. Не
# меняет semantic contract, не объединяет пары в один LLM-запрос —
# только убирает повторный cold-load.
#
# Отдельно измеряется (владелец): model load time, inference time,
# число LLM-вызовов, candidate count, time per candidate, total model
# reload count. Инструментация — обёртка urllib.request.urlopen ТОЛЬКО
# в этом diagnostic-скрипте (не меняет semantic_extract.py): читает
# load_duration/prompt_eval_duration/eval_duration из ответа Ollama
# (те же поля, что Ollama всегда возвращает, просто раньше отбрасывались
# `_call_ollama()`), не трогая исходные байты ответа для вызывающего кода.
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
# 7B, тот же size-class, что qwen2.5:7b — тот же проверенный потолок.
echo "=== временно поднимаем лимит ollama до 8g ==="
sudo docker update --memory=8g --memory-swap=8g "$(OLLAMA_CID)"
echo "=== ollama pull mistral:7b ==="
sudo docker compose exec -T ollama ollama pull mistral:7b

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
import time

import helm_core.knowledge.semantic_extract as semantic_extract
from helm_core.knowledge.relation_candidates import generate_candidates
from helm_core.knowledge.relation_classifier import classify_relation
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_benchmark_metrics import aggregate, evaluate_case
from helm_core.knowledge.semantic_extract import (
    ExtractionFailed, WindowExtraction, WindowTruncated, extract_window,
)

# ── инструментация: перехват сырого ответа Ollama ради load/inference ──
# времени и счётчика вызовов. НЕ меняет semantic_extract.py — обёртка
# только здесь, в diagnostic-скрипте; _call_ollama() и весь код выше
# получают те же байты ответа, что и без обёртки.
_call_log = []
_original_urlopen = semantic_extract.urllib.request.urlopen


class _ReplayedResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _instrumented_urlopen(request, timeout=None):
    response = _original_urlopen(request, timeout=timeout)
    raw = response.read()
    try:
        import json as _json
        payload = _json.loads(raw.decode())
        _call_log.append({
            "load_s": payload.get("load_duration", 0) / 1e9,
            "prompt_eval_s": payload.get("prompt_eval_duration", 0) / 1e9,
            "eval_s": payload.get("eval_duration", 0) / 1e9,
            "total_s": payload.get("total_duration", 0) / 1e9,
        })
    except Exception:
        _call_log.append({"load_s": 0.0, "prompt_eval_s": 0.0, "eval_s": 0.0, "total_s": 0.0})
    return _ReplayedResponse(raw)


semantic_extract.urllib.request.urlopen = _instrumented_urlopen

# Порог, отличающий «модель уже тёплая» (load_duration — доли секунды
# на служебные проверки) от настоящей перезагрузки весов (секунды).
RELOAD_THRESHOLD_S = 1.0

SKIP = {"long_dense_window"}
MODEL = "mistral:7b"
KEEP_ALIVE = "5m"  # владелец 03.09.2026: bounded warm lifecycle, не 0

cases = [c for c in GOLDEN_CASES if c.edges and c.case_id not in SKIP]
print(f"модель: {MODEL} (keep_alive={KEEP_ALIVE!r} — warm lifecycle)")
print(f"кейсов: {len(cases)}, всего gold-связей: {sum(len(c.edges) for c in cases)}")
print()

scores = []
total_candidates = total_none = total_rejected = total_accepted = 0
for case in cases:
    t0 = time.monotonic()
    calls_before_pass1 = len(_call_log)
    try:
        pass1 = extract_window(case.text, domain=case.domain, heading_path=case.heading_path,
                               model=MODEL, keep_alive=KEEP_ALIVE)
    except (WindowTruncated, ExtractionFailed) as exc:
        dt = time.monotonic() - t0
        print(f"{case.case_id}: ПРОПУЩЕН (pass 1) за {dt:.1f}с — {exc}")
        continue
    pass1_calls = _call_log[calls_before_pass1:]

    objects_by_id = {e.local_id: e for e in pass1.entities}
    objects_by_id.update({a.local_id: a for a in pass1.atoms})
    candidates = generate_candidates(pass1.entities, pass1.atoms, case.text)

    edges, rejected, none_count = [], list(pass1.rejected), 0
    calls_before_classify = len(_call_log)
    classify_t0 = time.monotonic()
    for candidate in candidates:
        edge, reason = classify_relation(
            candidate, from_obj=objects_by_id[candidate.from_id], to_obj=objects_by_id[candidate.to_id],
            model=MODEL, keep_alive=KEEP_ALIVE)
        if edge is not None:
            edges.append(edge)
        elif reason is not None:
            rejected.append(reason)
        else:
            none_count += 1
    classify_wall_s = time.monotonic() - classify_t0
    classify_calls = _call_log[calls_before_classify:]

    dt = time.monotonic() - t0
    extraction = WindowExtraction(entities=pass1.entities, atoms=pass1.atoms, edges=edges, rejected=rejected)
    score = evaluate_case(case, extraction)
    scores.append(score)
    total_candidates += len(candidates)
    total_none += none_count
    total_rejected += len(rejected) - len(pass1.rejected)
    total_accepted += len(edges)

    per_candidate_s = classify_wall_s / len(candidates) if candidates else 0.0
    case_reloads = sum(1 for c in pass1_calls + classify_calls if c["load_s"] > RELOAD_THRESHOLD_S)
    print(f"{case.case_id}: {dt:.1f}с — entities={len(extraction.entities)} atoms={len(extraction.atoms)} "
          f"candidates={len(candidates)} accepted={len(edges)} none={none_count} "
          f"rejected={len(rejected) - len(pass1.rejected)} — "
          f"edges_matched={score.edges_matched}/{score.edges_gold_scoreable} "
          f"relation_type_correct={score.relation_type_correct} extra={score.edges_extracted_extra} — "
          f"LLM-вызовов: pass1={len(pass1_calls)} classify={len(classify_calls)} "
          f"reloads={case_reloads} time/candidate={per_candidate_s:.2f}с")

print()
print(f"########## ИТОГО (C2, {MODEL}, warm keep_alive=5m, то же подмножество, что qwen2.5:7b run 217/220/222) ##########")
agg = aggregate(scores)
print(f"  кейсов оценено: {agg.cases_scored} из {len(cases)}")
print(f"  всего candidates={total_candidates} accepted={total_accepted} none={total_none} rejected={total_rejected}")
print(f"  typed relation_precision (нормативная, R4.6.B.1): {agg.relation_precision:.3f}")
print(f"  typed relation_recall: {agg.relation_recall:.3f}")
print(f"  typed relation_f1: {agg.relation_f1:.3f}")
print(f"  endpoint_relation_precision (диагностика): {agg.endpoint_relation_precision:.3f}")
print(f"  endpoint_relation_recall (диагностика): {agg.endpoint_relation_recall:.3f}")
print(f"  relation_type_accuracy: {agg.relation_type_accuracy:.3f}")
print(f"  entity_precision/recall: {agg.entity_precision:.3f}/{agg.entity_recall:.3f}")
print(f"  atom_precision/recall: {agg.atom_precision:.3f}/{agg.atom_recall:.3f}")
print(f"  total_material_hallucinations: {agg.total_material_hallucinations}")
print(f"  critical_entity_event_recall: {agg.critical_entity_event_recall:.3f}")
print()
print("  ---- владелец 03.09.2026: раздельные тайминги (load/inference/reload) ----")
total_calls = len(_call_log)
total_load_s = sum(c["load_s"] for c in _call_log)
total_infer_s = sum(c["prompt_eval_s"] + c["eval_s"] for c in _call_log)
total_reloads = sum(1 for c in _call_log if c["load_s"] > RELOAD_THRESHOLD_S)
print(f"  всего LLM-вызовов: {total_calls}")
print(f"  candidate count (сумма по кейсам): {total_candidates}")
print(f"  суммарное model load time: {total_load_s:.1f}с")
print(f"  суммарное inference time: {total_infer_s:.1f}с")
print(f"  total model reload count (load_duration > {RELOAD_THRESHOLD_S}с): {total_reloads} из {total_calls}")
print(f"  time per candidate (в среднем по всем classify-вызовам): "
      f"{(total_infer_s / total_candidates if total_candidates else 0.0):.2f}с inference "
      f"+ доля load ниже")
print()
print("  для сравнения — qwen2.5:7b C2 (run 222, keep_alive=0, БЕЗ этой инструментации):")
print("    typed_precision=0.103 typed_recall=0.273 relation_type_accuracy=0.857 (63 мин wall-clock)")
print()
print("  Только для справки (владелец п.7): полный 21-case golden запускается")
print("  ТОЛЬКО если typed precision здесь близко к 0.90 — иначе не тратим время.")
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
  echo "::error::диагностика завершилась с кодом $diag_rc"
  exit "$diag_rc"
fi
