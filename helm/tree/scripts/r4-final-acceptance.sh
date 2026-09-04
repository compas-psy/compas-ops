#!/bin/bash
# HELM v4.0 RESCUE · R4.7 final acceptance (владелец 04.09.2026) —
# ЕДИНСТВЕННЫЙ финальный live E2E acceptance run для уже выбранного
# `qwen2.5:7b` (docs/KNOWLEDGE_MODELS.md, «Выбор pass1 extractor»).
#
# Это НЕ r4-golden-benchmark.sh и НЕ его замена. Тот скрипт остаётся
# нетронутым историческим артефактом 3-candidate comparison (R4.4/R4.5.5)
# — здесь НЕ запускаются `gemma2:2b`/`qwen2.5:3b`, НЕ вызывается
# `select_winner()`/сравнительный ranking. `qwen2.5:7b` здесь не
# candidate — зафиксированная acceptance-конфигурация; единственный
# вопрос — проходит ли она ВСЕ §14.18 hard gates разом
# (`evaluate_hard_gates()`, переиспользован как есть через новый CLI
# `helm_core.knowledge.r4_final_acceptance evaluate`, ни строчки scoring
# не скопировано).
#
# Snapshot/restore и behavioral health-check логика мирроит
# r4-golden-benchmark.sh (тот же принцип: снимок ДО, EXIT trap
# восстанавливает РОВНО его после, что бы ни случилось) — не source'ится
# из него, чтобы не трогать уже проверенный живыми прогонами скрипт.
#
# Acceptance = строгое AND. Любой FAIL (гейт, отсутствующее измерение,
# исключение evaluator/compiler, недоказанный compiler provenance) —
# exit 1 (R4 BLOCKED), не "почти прошёл". PASS — exit 0 (R4 ACCEPTED).
# Ни то, ни другое не повторяется автоматически.
set -uo pipefail
cd /opt/helm/compose

MODEL="qwen2.5:7b"
SAFE=$(echo "$MODEL" | tr ':/.' '___')
BASE_DIR=/opt/helm-state/benchmarks/r4-final-acceptance
sudo mkdir -p "$BASE_DIR"
sudo chown "$(id -u):$(id -g)" "$BASE_DIR"

GIT_SHA=$(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo "unknown")
RUN_ID="r4final-$(date -u +%Y%m%dT%H%M%SZ)"
echo "выкачено: $GIT_SHA"
echo "run_id: $RUN_ID"
echo "acceptance-конфигурация (не candidate, не сравнение): $MODEL"

OLLAMA_CID() { sudo docker compose ps -q ollama; }

# ── Preflight, БЕЗ расходования live: структурные доказательства ──────
# Requirement 5/7/8: если это невозможно доказать программно — вообще
# не запускать. Это выполняется ДО единственного pull/inference, потому
# что сам pull/inference и есть тот live-ресурс, который нельзя тратить
# на прогон, обречённый быть недоказуемым по построению.
echo "############ PREFLIGHT: структурные доказательства (requirement 5/7/8) ############"
if ! sudo docker compose exec -T helm-core python3 -c "
from helm_core.knowledge.r4_final_acceptance import (
    verify_compiler_is_sole_edge_source, verify_zero_cloud_relation_extraction)
verify_compiler_is_sole_edge_source()
verify_zero_cloud_relation_extraction()
print('OK: compiler is sole edge source, zero-cloud invariant holds')
"; then
  echo "::error::структурные доказательства НЕ прошли — R4 BLOCKED, live acceptance НЕ запускается"
  exit 1
fi

container_memory_events_path() {
  local cid="$1" pid cg_path
  pid=$(sudo docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null)
  [ -z "$pid" ] || [ "$pid" = "0" ] && return 1
  cg_path=$(sudo awk -F: '$1=="0" {print $3}' "/proc/$pid/cgroup" 2>/dev/null)
  [ -z "$cg_path" ] && return 1
  cg_path="/sys/fs/cgroup${cg_path}/memory.events"
  sudo test -r "$cg_path" || return 1
  echo "$cg_path"
}

read_oom_kill_counter() {
  sudo awk '/^oom_kill /{print $2}' "$1" 2>/dev/null
}

# ── 1. Снимок ДО (тот же принцип, что r4-golden-benchmark.sh) ────────
WAS_OLLAMA_RUNNING=false
existing_cid=$(OLLAMA_CID)
if [ -n "$existing_cid" ] \
   && [ "$(sudo docker inspect -f '{{.State.Running}}' "$existing_cid" 2>/dev/null)" = "true" ]; then
  WAS_OLLAMA_RUNNING=true
fi
echo "ollama до acceptance: running=$WAS_OLLAMA_RUNNING"

sudo docker compose up -d ollama >/dev/null
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://127.0.0.1:11434/api/tags >/dev/null || { echo "::error::ollama API не поднялся"; exit 1; }

PREEXISTING_MODELS=$(sudo docker compose exec -T ollama ollama list | tail -n +2 | awk '{print $1}')
ORIGINAL_MEM_LIMIT=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$(OLLAMA_CID)")
if [ "$ORIGINAL_MEM_LIMIT" = "0" ] || [ -z "$ORIGINAL_MEM_LIMIT" ]; then
  ORIGINAL_MEM_LIMIT_HUMAN="4g"
else
  ORIGINAL_MEM_LIMIT_HUMAN="${ORIGINAL_MEM_LIMIT}b"
fi
echo "лимит памяти ollama до acceptance: $ORIGINAL_MEM_LIMIT_HUMAN"

SNAPSHOT_RESTORE_OK="unknown"

# ── 2. Восстановление — ЕДИНСТВЕННЫЙ trap ────────────────────────────
restore_ollama_state() {
  echo
  echo "############ ВОССТАНОВЛЕНИЕ ИСХОДНОГО СОСТОЯНИЯ OLLAMA ############"
  local cid
  cid=$(OLLAMA_CID)
  if [ -z "$cid" ]; then
    echo "  ollama-контейнер не найден — восстанавливать нечего"
    SNAPSHOT_RESTORE_OK="true"
    return
  fi

  sudo docker update --memory="$ORIGINAL_MEM_LIMIT_HUMAN" --memory-swap="$ORIGINAL_MEM_LIMIT_HUMAN" \
    "$cid" >/dev/null 2>&1 || echo "  ::warning::не удалось восстановить лимит памяти"

  local current_models m found ok=1
  current_models=$(sudo docker compose exec -T ollama ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')
  for m in $current_models; do
    found=0
    for p in $PREEXISTING_MODELS; do
      [ "$m" = "$p" ] && found=1 && break
    done
    [ "$found" -eq 0 ] && sudo docker compose exec -T ollama ollama rm "$m" >/dev/null 2>&1 || true
  done
  local after m2
  after=$(sudo docker compose exec -T ollama ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')
  for m2 in $PREEXISTING_MODELS; do
    if ! echo "$after" | grep -qxF "$m2"; then
      echo "  ::error::$m2 была до acceptance и ОТСУТСТВУЕТ после восстановления — pull заново"
      sudo docker compose exec -T ollama ollama pull "$m2" || true
      ok=0
    fi
  done

  if [ "$WAS_OLLAMA_RUNNING" = "true" ]; then
    sudo docker compose up -d ollama >/dev/null 2>&1
  else
    sudo docker compose stop ollama >/dev/null 2>&1
  fi
  SNAPSHOT_RESTORE_OK=$([ "$ok" -eq 1 ] && echo "true" || echo "false")
  echo "  snapshot_restore_ok=$SNAPSHOT_RESTORE_OK"
}
trap restore_ollama_state EXIT

# ── 3. Поведенческое здоровье (тот же принцип) ───────────────────────
check_helm_core_healthz() {
  sudo docker compose exec -T helm-core python3 -c "
import urllib.request
try:
    r = urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=5)
    print('OK' if r.status == 200 else f'FAIL status={r.status}')
except Exception as e:
    print(f'FAIL {e}')
" 2>&1 | tail -1
}

check_postgres_query() {
  local out
  out=$(sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc "select 1" 2>&1)
  [ "$(echo "$out" | tr -d '[:space:]')" = "1" ] && echo "OK" || echo "FAIL $out"
}

HEALTH_BEFORE_CORE=$(check_helm_core_healthz)
HEALTH_BEFORE_PG=$(check_postgres_query)
echo "############ ЗДОРОВЬЕ ДО ACCEPTANCE ############"
echo "helm-core: $HEALTH_BEFORE_CORE   postgres: $HEALTH_BEFORE_PG"

# ── 4. Resource preflight — БЛОКИРУЕТ, не пропускает (это не пул кандидатов) ──
AVAILABLE_MB=$(free -m | awk '/^Mem:/ {print $7}')
echo "доступно сейчас: ${AVAILABLE_MB}MiB"
if [ "$AVAILABLE_MB" -lt 6144 ]; then
  echo "::error::доступно ${AVAILABLE_MB}MiB < 6144MiB — resource preflight не пройден, R4 BLOCKED (не пропуск, acceptance не для кого пропускать)"
  exit 1
fi
sudo docker update --memory=8g --memory-swap=8g "$(OLLAMA_CID)"

LITELLM_LOG_LINES_BEFORE=$(sudo docker compose logs litellm 2>/dev/null | wc -l)

echo "############ ollama pull $MODEL ############"
sudo docker compose exec -T ollama ollama pull "$MODEL"
DIGEST=$(sudo docker compose exec -T ollama ollama list | awk -v m="$MODEL" '$1==m {print $2}')
echo "digest: $DIGEST"

# ── 5. Ресурсный сэмплер (тот же принцип: фон, максимум за весь прогон) ──
started_before=$(sudo docker inspect -f '{{.State.StartedAt}}' "$(OLLAMA_CID)")
restart_before=$(sudo docker inspect -f '{{.RestartCount}}' "$(OLLAMA_CID)")
mem_events_path=$(container_memory_events_path "$(OLLAMA_CID)") || mem_events_path=""
if [ -n "$mem_events_path" ]; then
  oom_kill_before=$(read_oom_kill_counter "$mem_events_path") || oom_kill_before=""
else
  oom_kill_before=""
  echo "::warning::cgroup v2 memory.events недоступен — OOM-инструментовка провалится явно"
fi

sample_file=$(mktemp)
(
  while true; do
    ts=$(date +%s.%N)
    stats_line=$(sudo docker stats --no-stream "$(OLLAMA_CID)" --format "{{.MemUsage}}|{{.CPUPerc}}" 2>/dev/null)
    raw_rss=$(echo "$stats_line" | cut -d'|' -f1 | cut -d/ -f1 | tr -d ' ')
    raw_cpu=$(echo "$stats_line" | cut -d'|' -f2 | tr -d ' ')
    avail=$(free -m | awk '/^Mem:/ {print $7}')
    swap=$(free -m | awk '/^Swap:/ {print $3}')
    if [ -n "$raw_rss" ] && [ -n "$raw_cpu" ]; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$ts" "$raw_rss" "$raw_cpu" "$avail" "$swap" >> "$sample_file"
    fi
    sleep 2
  done
) &
sampler_pid=$!
t_total_start=$(date +%s.%N)

# ── 6. keep_alive probe — те же поля обязательны для evaluate_hard_gates() ──
echo "############ keep_alive: cold(0) vs warm(5m) ############"
ka_out="$BASE_DIR/keepalive-$SAFE.log"
{
  for ka in 0 5m; do
    echo "--- keep_alive=$ka ---"
    for i in 1 2 3 4 5; do
      t0=$(date +%s.%N)
      sudo docker compose exec -T helm-core \
        python3 -m helm_core.knowledge.semantic_benchmark golden \
        --model "$MODEL" --keep-alive "$ka" --stability-repeats 1 \
        --case doctor_visit > /dev/null 2>> "${ka_out%.log}.stderr.log"
      t1=$(date +%s.%N)
      elapsed=$(python3 -c "print(round($t1 - $t0, 2))")
      rss=$(sudo docker stats --no-stream "$(OLLAMA_CID)" --format "{{.MemUsage}}")
      echo "  прогон $i: ${elapsed}с, RSS: $rss"
    done
  done
} 2>&1 | tee "$ka_out"
COLD0=$(grep -A1 "keep_alive=0" "$ka_out" | grep "прогон 1:" | grep -oP '\d+\.\d+(?=с)')
WARM0=$(grep -A6 "keep_alive=5m" "$ka_out" | grep "прогон 5:" | grep -oP '\d+\.\d+(?=с)')

# ── 7. Канонический golden benchmark, fingerprint + validate (тот же принцип) ──
echo "############ golden benchmark, keep_alive=0, каноническая ревизия ############"
FP_JSON=$(sudo docker compose exec -T helm-core \
  python3 -m helm_core.knowledge.semantic_benchmark fingerprint \
  --model "$MODEL" --keep-alive "0" --git-sha "$GIT_SHA" --model-digest "$DIGEST")
FP_HASH=$(echo "$FP_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['fingerprint_hash'])")
RUN_DIR="$BASE_DIR/$SAFE-${FP_HASH:0:16}"
sudo mkdir -p "$RUN_DIR"
sudo chown "$(id -u):$(id -g)" "$RUN_DIR"
RESULT_JSON="$RUN_DIR/result.json"
TMP_OUT="$RESULT_JSON.tmp"
echo "$FP_JSON" > "$RUN_DIR/fingerprint.json"

GOLDEN_RC=0
if [ -s "$RESULT_JSON" ] && sudo docker compose exec -T helm-core \
     python3 -m helm_core.knowledge.semantic_benchmark validate \
     --file "/dev/stdin" --expect-model "$MODEL" --expect-fingerprint-hash "$FP_HASH" \
     < "$RESULT_JSON" 2>&1; then
  echo "-- reuse: fingerprint совпал, канонический прогон уже есть в $RESULT_JSON --"
else
  sudo docker compose exec -T helm-core \
    python3 -m helm_core.knowledge.semantic_benchmark golden \
    --model "$MODEL" --keep-alive "0" --stability-repeats 3 \
    --git-sha "$GIT_SHA" --model-digest "$DIGEST" \
    > "$TMP_OUT" 2> "$RUN_DIR/stderr.log"
  GOLDEN_RC=$?
  if [ "$GOLDEN_RC" -ne 0 ]; then
    echo "::error::golden benchmark завершился с кодом $GOLDEN_RC — см. $RUN_DIR/stderr.log"
  elif ! sudo docker compose exec -T helm-core \
         python3 -m helm_core.knowledge.semantic_benchmark validate \
         --file /dev/stdin --expect-model "$MODEL" --expect-fingerprint-hash "$FP_HASH" \
         < "$TMP_OUT"; then
    echo "::error::result.json.tmp не прошёл валидацию — НЕ публикуется"
    GOLDEN_RC=1
  else
    mv "$TMP_OUT" "$RESULT_JSON"
    echo "-- $RESULT_JSON записан и провалидирован --"
  fi
fi

t_total_end=$(date +%s.%N)
TOTAL_SECONDS=$(python3 -c "print(round($t_total_end - $t_total_start, 2))")
kill "$sampler_pid" 2>/dev/null
wait "$sampler_pid" 2>/dev/null

# ── 8. Ресурсные метрики + OOM delta (тот же принцип) ────────────────
PEAK_JSON=$(sudo docker compose exec -T helm-core \
  python3 -m helm_core.knowledge.resource_sampling peak-stats < "$sample_file")
rm -f "$sample_file"

started_after=$(sudo docker inspect -f '{{.State.StartedAt}}' "$(OLLAMA_CID)")
restart_after=$(sudo docker inspect -f '{{.RestartCount}}' "$(OLLAMA_CID)")
if [ -n "$mem_events_path" ] && sudo test -r "$mem_events_path"; then
  oom_kill_after=$(read_oom_kill_counter "$mem_events_path") || oom_kill_after=""
else
  oom_kill_after=""
fi

HEALTH_AFTER_CORE=$(check_helm_core_healthz)
HEALTH_AFTER_PG=$(check_postgres_query)
DEGRADED="false"
[ "$HEALTH_BEFORE_CORE" = "OK" ] && [ "$HEALTH_AFTER_CORE" != "OK" ] && DEGRADED="true"
[ "$HEALTH_BEFORE_PG" = "OK" ] && [ "$HEALTH_AFTER_PG" != "OK" ] && DEGRADED="true"
echo "############ ЗДОРОВЬЕ ПОСЛЕ ACCEPTANCE ############"
echo "helm-core: до=$HEALTH_BEFORE_CORE после=$HEALTH_AFTER_CORE   postgres: до=$HEALTH_BEFORE_PG после=$HEALTH_AFTER_PG   other_services_degraded=$DEGRADED"

PEAK_JSON_FILE=$(mktemp)
echo "$PEAK_JSON" > "$PEAK_JSON_FILE"
RESOURCES_JSON="$BASE_DIR/resources-$SAFE.json"
python3 - "$MODEL" "$DIGEST" "${COLD0:-}" "${WARM0:-}" "$TOTAL_SECONDS" \
         "$started_before" "$restart_before" "${oom_kill_before:-}" \
         "$started_after" "$restart_after" "${oom_kill_after:-}" "$DEGRADED" "$PEAK_JSON_FILE" \
         > "$RESOURCES_JSON" <<'PYEOF'
import json
import sys

(model, digest, cold, warm, total, started_before, restart_before, oom_kill_before,
 started_after, restart_after, oom_kill_after, degraded, peak_file) = sys.argv[1:14]
with open(peak_file) as f:
    peak = json.load(f)


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


if restart_before != restart_after:
    oom_occurred = True
else:
    b, a = to_float(oom_kill_before), to_float(oom_kill_after)
    oom_occurred = (a > b) if (b is not None and a is not None) else None

print(json.dumps({
    "model": model, "model_digest": digest,
    "cold_latency_seconds": to_float(cold), "warm_latency_seconds": to_float(warm),
    "total_benchmark_seconds": to_float(total),
    "peak_rss_mb": peak["peak_rss_mb"], "peak_cpu_percent": peak["peak_cpu_percent"],
    "swap_before_mb": peak["swap_before_mb"], "swap_peak_mb": peak["swap_peak_mb"],
    "swap_after_mb": peak["swap_after_mb"],
    "oom_occurred": oom_occurred,
    "other_services_degraded": degraded == "true",
    "keep_alive_policy": "0",
    "min_host_available_ram_mb": peak["min_host_available_ram_mb"],
    "resource_samples_count": peak["samples_count"],
}, indent=2))
PYEOF
rm -f "$PEAK_JSON_FILE"
cat "$RESOURCES_JSON"

LITELLM_LOG_LINES_AFTER=$(sudo docker compose logs litellm 2>/dev/null | wc -l)
LITELLM_DELTA_LINES=$((LITELLM_LOG_LINES_AFTER - LITELLM_LOG_LINES_BEFORE))
echo "строк лога litellm: было $LITELLM_LOG_LINES_BEFORE, стало $LITELLM_LOG_LINES_AFTER (best-effort, structural AST-инвариант — единственная твёрдая гарантия, см. zero_cloud_relation_extraction в самом артефакте)"

# ── 9. R4.7 final acceptance evaluate — единственная точка вердикта ──
# helm-core не монтирует BASE_DIR (та же причина, что у
# r4-evaluate-hard-gates.sh) — result.json и resources-<model>.json
# читаются на ХОСТЕ и передаются внутрь ОДНИМ JSON через stdin
# (`--combined /dev/stdin`, r4_final_acceptance.py), а не как два
# отдельных --result/--resources пути внутри контейнера.
ACCEPTANCE_JSON="$BASE_DIR/R4_FINAL_ACCEPTANCE.json"
ACCEPTANCE_RC=0
if [ "$GOLDEN_RC" -ne 0 ]; then
  echo "::error::golden benchmark не дал валидный result.json — R4 BLOCKED без вызова evaluate"
  python3 -c "
import json
json.dump({
    'run_id': '$RUN_ID', 'git_sha': '$GIT_SHA', 'model': '$MODEL', 'model_digest': '$DIGEST',
    'overall_pass': False, 'error': 'golden benchmark did not produce a valid result.json (rc=$GOLDEN_RC)',
}, open('$ACCEPTANCE_JSON', 'w'), indent=2, ensure_ascii=False)
"
  ACCEPTANCE_RC=1
else
  COMBINED_INPUT=$(mktemp)
  python3 -c "
import json
result = json.load(open('$RESULT_JSON'))
resources = json.load(open('$RESOURCES_JSON'))
json.dump({'result': result, 'resources': resources}, open('$COMBINED_INPUT', 'w'))
"
  sudo docker compose exec -T helm-core \
    python3 -m helm_core.knowledge.r4_final_acceptance evaluate \
    --combined /dev/stdin --litellm-calls 0 --openrouter-calls 0 \
    --git-sha "$GIT_SHA" --model-digest "$DIGEST" --run-id "$RUN_ID" \
    < "$COMBINED_INPUT" > "$ACCEPTANCE_JSON.tmp"
  ACCEPTANCE_RC=$?
  rm -f "$COMBINED_INPUT"

  python3 -c "
import json
d = json.load(open('$ACCEPTANCE_JSON.tmp'))
d['snapshot_restore'] = {'restored_ok': None}  # заполняется ниже, после restore_ollama_state
d['health_check'] = {
    'before': {'helm_core': '$HEALTH_BEFORE_CORE', 'postgres': '$HEALTH_BEFORE_PG'},
    'after': {'helm_core': '$HEALTH_AFTER_CORE', 'postgres': '$HEALTH_AFTER_PG'},
}
d['litellm_log_lines_delta'] = $LITELLM_DELTA_LINES
json.dump(d, open('$ACCEPTANCE_JSON', 'w'), indent=2, ensure_ascii=False)
"
  rm -f "$ACCEPTANCE_JSON.tmp"
fi

echo
echo "############ ЯВНОЕ ВОССТАНОВЛЕНИЕ ПЕРЕД ФИНАЛЬНОЙ ПРОВЕРКОЙ ############"
restore_ollama_state

# Requirement 8/10: снэпшот, оставленный невосстановленным, — это
# сломанный живой сервер, а не отдельная от acceptance проблема. Не
# "DEGRADED-as-success" — сбой restore переводит итог в FAIL независимо
# от того, что показали §14.18 gates, и меняет ОБА места истины разом
# (exit code процесса и `overall_pass` внутри самого артефакта), чтобы
# они не разошлись.
if [ "$SNAPSHOT_RESTORE_OK" != "true" ]; then
  echo "::error::snapshot restore НЕ подтверждён (snapshot_restore_ok=$SNAPSHOT_RESTORE_OK) — R4 BLOCKED независимо от gates"
  ACCEPTANCE_RC=1
fi
python3 -c "
import json
d = json.load(open('$ACCEPTANCE_JSON'))
restored_ok = $([ "$SNAPSHOT_RESTORE_OK" = "true" ] && echo True || echo False)
d.setdefault('snapshot_restore', {})['restored_ok'] = restored_ok
if not restored_ok:
    d['overall_pass'] = False
    d['error'] = (d.get('error') or '') + ' | snapshot restore not confirmed'
json.dump(d, open('$ACCEPTANCE_JSON', 'w'), indent=2, ensure_ascii=False)
" 2>/dev/null || true

echo
echo "############ R4.7 FINAL ACCEPTANCE — ИТОГ ############"
python3 -c "
import json
d = json.load(open('$ACCEPTANCE_JSON'))
print('overall_pass:', d.get('overall_pass'))
print('hard_gate_passed:', d.get('hard_gate_passed'))
for v in d.get('hard_gate_violations', []):
    print('  VIOLATION:', v)
for c in d.get('checks', []):
    status = 'OK' if c.get('passed') else 'FAIL'
    print(f\"  check[{status}]: {c.get('name')}\", ('— ' + c['detail']) if c.get('detail') else '')
print('proposed_edges_total:', d.get('proposed_edges_total'),
      'compiled_edges_total:', d.get('compiled_edges_total'),
      'scored_edges_total:', d.get('scored_edges_total'))
print('error:', d.get('error'))
"
sudo find "$BASE_DIR" -maxdepth 2 -type f | sort

if [ "$ACCEPTANCE_RC" -eq 0 ]; then
  echo "############ R4 ACCEPTED ############"
else
  echo "############ R4 BLOCKED ############"
fi
exit "$ACCEPTANCE_RC"
