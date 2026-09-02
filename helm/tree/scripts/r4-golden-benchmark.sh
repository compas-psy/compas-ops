#!/bin/bash
# HELM v4.0 RESCUE · R4 — живой прогон golden-бенчмарка + сравнение
# keep_alive на реальных кандидатах (§14.18, R4 пп.3-6).
#
# Ретракция владельца 02.09.2026 (после #185-#187): скрипт БЕЗУСЛОВНО
# гасил ollama и удалял ВСЕ модели кандидатов в конце, включая gemma2:2b
# — боевую модель Z2 style-rephrase (rephrase.py), которая была на
# сервере ДО бенчмарка. Раз ollama входит в обычный `deploy` (docker-
# compose.yml: «сервис входит в обычный deploy... up -d ollama + ollama
# pull gemma2:2b идемпотентно на каждый normal deploy»), она обязана
# быть в этом же состоянии и ПОСЛЕ бенчмарка — не только «в целом
# работает после следующего деплоя».
#
# Правило теперь простое и без исключений: снять точный снимок ДО
# (что запущено, что скачано, какой лимит памяти) — и вернуть РОВНО его
# после, что бы ни случилось внутри (успех, провал кандидата, обрыв
# соединения — EXIT trap видит все три случая одинаково).
#
# Кандидаты выбраны по r4-inventory.sh: gemma2:2b (обязательный
# baseline, уже на сервере), qwen2.5:3b (современный 3-4B, уже проверен
# pullable на этом VPS для Z2), qwen2.5:7b (сильный 7-8B — только если
# живой resource preflight прямо перед попыткой показывает свободных
# >=6GiB, иначе пропускается).
#
# Идемпотентность больше не «файл существует» (найдено BLOCKER-ом:
# частично записанный/устаревший результат мог сойти за готовый) — а
# fingerprint (R4 п.4): каталог результата назван по хэшу от git SHA +
# SHA256 кода извлечения/промпта/схемы/фикстур/харнесса + seed + модель
# + digest + keep_alive. Разные входы — разные каталоги; те же входы —
# тот же каталог, и уже лежащий там результат проверяется ЗАНОВО
# (`validate`), а не просто «файл есть».
set -uo pipefail
cd /opt/helm/compose

BASE_DIR=/opt/helm-state/benchmarks/r4
sudo mkdir -p "$BASE_DIR"
sudo chown "$(id -u):$(id -g)" "$BASE_DIR"

# Кандидаты, чей канонический golden benchmark не дал валидный
# result.json — job должен закончиться красным, а не молча зелёным
# (см. фикс run_id/run_candidate() ниже).
CANDIDATE_FAILURES=""

GIT_SHA=$(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo "unknown")
echo "выкачено: $GIT_SHA"

OLLAMA_CID() { sudo docker compose ps -q ollama; }

# ── 1. Снимок ДО (владелец п.1-2) ────────────────────────────────────
WAS_OLLAMA_RUNNING=false
existing_cid=$(OLLAMA_CID)
if [ -n "$existing_cid" ] \
   && [ "$(sudo docker inspect -f '{{.State.Running}}' "$existing_cid" 2>/dev/null)" = "true" ]; then
  WAS_OLLAMA_RUNNING=true
fi
echo "ollama до бенчмарка: running=$WAS_OLLAMA_RUNNING"

sudo docker compose up -d ollama >/dev/null
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://127.0.0.1:11434/api/tags >/dev/null || { echo "::error::ollama API не поднялся"; exit 1; }

PREEXISTING_MODELS=$(sudo docker compose exec -T ollama ollama list | tail -n +2 | awk '{print $1}')
echo "модели до бенчмарка:"
echo "$PREEXISTING_MODELS" | sed 's/^/  /'

ORIGINAL_MEM_LIMIT=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$(OLLAMA_CID)")
# Docker отдаёт лимит в байтах (0 = не задан). compose объявляет 4g —
# используем его как читаемый дефолт, если рантайм почему-то вернул 0
# (например, ollama только что поднята этим же скриптом с нуля).
if [ "$ORIGINAL_MEM_LIMIT" = "0" ] || [ -z "$ORIGINAL_MEM_LIMIT" ]; then
  ORIGINAL_MEM_LIMIT_HUMAN="4g"
else
  ORIGINAL_MEM_LIMIT_HUMAN="${ORIGINAL_MEM_LIMIT}b"
fi
echo "лимит памяти ollama до бенчмарка: $ORIGINAL_MEM_LIMIT_HUMAN"

# ── 2. Восстановление — ЕДИНСТВЕННЫЙ trap, что бы ни случилось ──────
restore_ollama_state() {
  echo
  echo "############ ВОССТАНОВЛЕНИЕ ИСХОДНОГО СОСТОЯНИЯ OLLAMA ############"
  local cid
  cid=$(OLLAMA_CID)
  if [ -z "$cid" ]; then
    echo "  ollama-контейнер не найден — восстанавливать нечего"
    return
  fi

  echo "-- лимит памяти -> $ORIGINAL_MEM_LIMIT_HUMAN --"
  sudo docker update --memory="$ORIGINAL_MEM_LIMIT_HUMAN" --memory-swap="$ORIGINAL_MEM_LIMIT_HUMAN" \
    "$cid" >/dev/null 2>&1 || echo "  ::warning::не удалось восстановить лимит памяти"

  echo "-- модели: удаляем ТОЛЬКО то, чего не было до бенчмарка --"
  local current_models m found
  current_models=$(sudo docker compose exec -T ollama ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')
  for m in $current_models; do
    found=0
    for p in $PREEXISTING_MODELS; do
      [ "$m" = "$p" ] && found=1 && break
    done
    if [ "$found" -eq 0 ]; then
      echo "  rm $m (появилась во время бенчмарка)"
      sudo docker compose exec -T ollama ollama rm "$m" >/dev/null 2>&1 || true
    else
      echo "  оставляем $m (была до бенчмарка)"
    fi
  done
  echo "-- проверка: все preexisting модели на месте --"
  local after m2 ok=1
  after=$(sudo docker compose exec -T ollama ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')
  for m2 in $PREEXISTING_MODELS; do
    if ! echo "$after" | grep -qxF "$m2"; then
      echo "  ::error::$m2 была до бенчмарка и ОТСУТСТВУЕТ после восстановления — pull заново"
      sudo docker compose exec -T ollama ollama pull "$m2" || true
      ok=0
    fi
  done
  [ "$ok" -eq 1 ] && echo "  все preexisting модели на месте"

  echo "-- running/stopped: было running=$WAS_OLLAMA_RUNNING --"
  if [ "$WAS_OLLAMA_RUNNING" = "true" ]; then
    sudo docker compose up -d ollama >/dev/null 2>&1
    echo "  ollama оставлена/возвращена запущенной"
  else
    sudo docker compose stop ollama >/dev/null 2>&1
    echo "  ollama остановлена (было остановлено до бенчмарка)"
  fi
}
trap restore_ollama_state EXIT

# ── 3. Поведенческое здоровье HELM (владелец п.6, не только `ps`) ───
# Возвращают буквально "OK" или "FAIL ..." — сравнимо до/после кандидата,
# не просто печатают в лог.
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

# НАЙДЕНО живым прогоном 02.09.2026: смоук через probe() с этим текстом
# НИКОГДА не проверял Z2 честно — probe.py:397 зовёт rephrase_or_none()
# только при mode=="Z0" (ровно одна evidence-запись); решение владельца
# 01.09.2026 сделало общий поиск глобальным по корпусу (probe.py:125-142,
# health включён), и этот вопрос против реального корпуса предсказуемо
# цепляет несколько посторонних совпадений → mode=Z1 → рефраз не
# вызывается вообще, независимо от здоровья Ollama. Прямой вызов
# rephrase() в обход retrieval — единственная честная проверка.
z2_rephrase_smoke() {
  sudo docker compose exec -T helm-core python3 <<'PY'
from helm_core.knowledge.rephrase import rephrase, RephraseUnavailable
try:
    text = rephrase(
        "что такое схема?",
        "Схема — это устойчивый паттерн мышления и поведения, сформированный в детстве.",
        system_prompt=None,
    )
    print("Z2_DIRECT: OK")
    print("answer_text:", repr(text))
except RephraseUnavailable as exc:
    print("Z2_DIRECT: FAIL", repr(str(exc)))
PY
}

behavioral_health_check() {
  local label="$1"
  echo "-- $label: контейнеры --"
  sudo docker compose ps --format "{{.Service}}: {{.Status}}"
  echo "-- $label: helm-core /healthz прямо сейчас: $(check_helm_core_healthz) --"
  echo "-- $label: postgres реальным запросом: $(check_postgres_query) --"
  echo "-- $label: swap --"
  free -m | awk '/^Swap:/ {print "  " $0}'
  echo "-- $label: restart count --"
  for svc in helm-core postgres litellm ollama; do
    local cid
    cid=$(sudo docker compose ps -q "$svc" 2>/dev/null)
    [ -n "$cid" ] && echo "  $svc: $(sudo docker inspect -f '{{.RestartCount}}' "$cid")"
  done
}

echo "############ ЗДОРОВЬЕ ДО БЕНЧМАРКА (поведенчески) ############"
behavioral_health_check "ДО"
echo
echo "############ Z2 REPHRASE SMOKE ДО БЕНЧМАРКА ############"
z2_rephrase_smoke

#: ollama pull/exec на каждый кандидат уходит в лог LiteLLM/OpenRouter-
#: активности как «до/после» окно (владелец п.7) — точных счётчиков
#: запросов у этого стека нет, поэтому берётся число строк лога за
#: контейнером как best-effort runtime evidence поверх structural
#: AST-инварианта (test_extraction_never_leaves_the_machine), а не
#: вместо него.
LITELLM_LOG_LINES_BEFORE=$(sudo docker compose logs litellm 2>/dev/null | wc -l)

run_golden_canonical() {
  local model="$1" digest="$2" keep_alive="$3"
  local fp_json fp_hash run_dir tmp_out out

  fp_json=$(sudo docker compose exec -T helm-core \
    python3 -m helm_core.knowledge.semantic_benchmark fingerprint \
    --model "$model" --keep-alive "$keep_alive" --git-sha "$GIT_SHA" --model-digest "$digest")
  fp_hash=$(echo "$fp_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['fingerprint_hash'])")
  run_dir="$BASE_DIR/$(echo "$model" | tr ':/.' '___')-${fp_hash:0:16}"
  sudo mkdir -p "$run_dir"
  sudo chown "$(id -u):$(id -g)" "$run_dir"
  out="$run_dir/result.json"
  tmp_out="$run_dir/result.json.tmp"
  echo "$fp_json" > "$run_dir/fingerprint.json"

  if [ -s "$out" ]; then
    echo "-- существующий результат найден, проверяю fingerprint --"
    if sudo docker compose exec -T helm-core \
         python3 -m helm_core.knowledge.semantic_benchmark validate \
         --file "/dev/stdin" --expect-model "$model" --expect-fingerprint-hash "$fp_hash" \
         < "$out" 2>&1; then
      echo "-- reuse: fingerprint совпал, канонический прогон уже есть в $out --"
      return 0
    fi
    echo "-- существующий результат не прошёл валидацию под текущим fingerprint — прогоняю заново --"
  fi

  echo "-- golden benchmark (keep_alive=$keep_alive, каноническая ревизия) --"
  # НАЙДЕНО живым прогоном 196 (два кандидата подряд, детерминированно):
  # --run-id здесь раньше передавал ${fp_hash:0:16} — производную от
  # fp_hash, вычисленного ВЫШЕ БЕЗ --run-id (по умолчанию run_id=""). Раз
  # run_id сам входит в compute_fingerprint(), эти два вызова считали
  # fingerprint с разными входами и НИКОГДА не могли совпасть — validate
  # проваливался на каждом кандидате безусловно, не изредка. run_id
  # должен быть тем же, что использован для fp_hash/run_dir/expect —
  # то есть тоже отсутствовать (пусто), а не выводиться из его же хэша.
  sudo docker compose exec -T helm-core \
    python3 -m helm_core.knowledge.semantic_benchmark golden \
    --model "$model" --keep-alive "$keep_alive" --stability-repeats 3 \
    --git-sha "$GIT_SHA" --model-digest "$digest" \
    > "$tmp_out" 2> "$run_dir/stderr.log"
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "::error::extraction завершилась с кодом $rc — см. $run_dir/stderr.log"
    rm -f "$tmp_out"
    return 1
  fi

  if ! sudo docker compose exec -T helm-core \
         python3 -m helm_core.knowledge.semantic_benchmark validate \
         --file /dev/stdin --expect-model "$model" --expect-fingerprint-hash "$fp_hash" \
         < "$tmp_out"; then
    echo "::error::result.json.tmp не прошёл валидацию — НЕ публикуется как result.json"
    return 1
  fi
  mv "$tmp_out" "$out"
  echo "-- $out записан и провалидирован --"
}

run_keepalive_probe() {
  local model="$1" out="$2"
  # tee — держит SSH-трафик живым при долгом молчаливом выводе (найдено
  # живым прогоном 02.09.2026: без него соединение рвётся как
  # «простаивающее», хотя работа идёт).
  {
    for ka in 0 5m; do
      echo "--- keep_alive=$ka ---"
      for i in 1 2 3 4 5; do
        t0=$(date +%s.%N)
        sudo docker compose exec -T helm-core \
          python3 -m helm_core.knowledge.semantic_benchmark golden \
          --model "$model" --keep-alive "$ka" --stability-repeats 1 \
          --case doctor_visit > /dev/null 2>> "${out%.log}.stderr.log"
        t1=$(date +%s.%N)
        elapsed=$(python3 -c "print(round($t1 - $t0, 2))")
        rss=$(sudo docker stats --no-stream "$(OLLAMA_CID)" --format "{{.MemUsage}}")
        echo "  прогон $i: ${elapsed}с, RSS: $rss"
      done
    done
  } 2>&1 | tee "$out"
}

run_candidate() {
  local model="$1" safe
  safe=$(echo "$model" | tr ':/.' '___')

  echo "=========================================="
  echo "=== кандидат: $model ==="
  echo "=========================================="
  echo "=== ollama pull ==="
  sudo docker compose exec -T ollama ollama pull "$model"
  local digest
  digest=$(sudo docker compose exec -T ollama ollama list | awk -v m="$model" '$1==m {print $2}')
  echo "digest: $digest"

  local health_before_core health_before_pg
  health_before_core=$(check_helm_core_healthz)
  health_before_pg=$(check_postgres_query)

  # Фоновый опрос swap на время реальной работы — снимок "до/после" сам
  # по себе пропускает транзиентный пик посередине (владелец п.6: "swap
  # before/peak/after" — три разных числа, не два).
  local swap_poll_file swap_poll_pid
  swap_poll_file=$(mktemp)
  ( while true; do free -m | awk '/^Swap:/ {print $3}' >> "$swap_poll_file"; sleep 2; done ) &
  swap_poll_pid=$!

  local t_total_start t_total_end
  t_total_start=$(date +%s.%N)

  echo "=== keep_alive: cold(0) vs warm(5m), измеряется ПЕРВЫМ — до основного прогона ==="
  local ka_out="$BASE_DIR/keepalive-$safe.log"
  run_keepalive_probe "$model" "$ka_out"
  local cold0 warm0
  cold0=$(grep -A1 "keep_alive=0" "$ka_out" | grep "прогон 1:" | grep -oP '\d+\.\d+(?=с)')
  warm0=$(grep -A6 "keep_alive=5m" "$ka_out" | grep "прогон 5:" | grep -oP '\d+\.\d+(?=с)')

  # НАЙДЕНО живым прогоном 196: возврат run_golden_canonical() раньше
  # никак не проверялся — реальный провал валидации (см. фикс run_id
  # выше) молча тонул, а run_candidate() продолжал как ни в чём не
  # бывало до конца (resources/health/Z2), из-за чего job зелёный, хотя
  # кандидат без валидного result.json НЕ участвует в select_winner().
  if ! run_golden_canonical "$model" "$digest" "0"; then
    echo "::error::$model: канонический golden benchmark не создал валидный result.json"
    CANDIDATE_FAILURES="$CANDIDATE_FAILURES $model"
  fi

  t_total_end=$(date +%s.%N)
  local total_seconds
  total_seconds=$(python3 -c "print(round($t_total_end - $t_total_start, 2))")

  kill "$swap_poll_pid" 2>/dev/null
  wait "$swap_poll_pid" 2>/dev/null
  local swap_before swap_peak swap_after oom_flag raw_mem raw_cpu
  swap_before=$(head -1 "$swap_poll_file" 2>/dev/null)
  swap_peak=$(sort -n "$swap_poll_file" 2>/dev/null | tail -1)
  swap_after=$(free -m | awk '/^Swap:/ {print $3}')
  rm -f "$swap_poll_file"

  echo "=== ресурсы: RSS/CPU/swap/OOM ==="
  raw_mem=$(sudo docker stats --no-stream "$(OLLAMA_CID)" --format "{{.MemUsage}}")
  raw_cpu=$(sudo docker stats --no-stream "$(OLLAMA_CID)" --format "{{.CPUPerc}}")
  # State.OOMKilled — то, что реально знает ядро об ЭТОМ контейнере, не
  # догадка по уровню swap (владелец п.6: "OOM evidence", не "похоже на OOM").
  oom_flag=$(sudo docker inspect -f '{{.State.OOMKilled}}' "$(OLLAMA_CID)" 2>/dev/null || echo "unknown")

  python3 - "$model" "$digest" "${cold0:-}" "${warm0:-}" "$total_seconds" \
           "$raw_mem" "$raw_cpu" "${swap_before:-}" "${swap_peak:-}" "${swap_after:-}" "$oom_flag" "0" \
           > "$BASE_DIR/resources-$safe.json" <<'PYEOF'
import json
import re
import sys

(model, digest, cold, warm, total, raw_mem, raw_cpu,
 swap_before, swap_peak, swap_after, oom_flag, keep_alive) = sys.argv[1:13]


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_size_mb(text):
    m = re.match(r"([\d.]+)\s*([A-Za-z]+)", text.strip())
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2)
    mult = {"B": 1e-6, "KB": 1e-3, "KiB": 1.048576e-3 * 1000 / 1024,
            "MB": 1.0, "MiB": 1.048576, "GB": 1000.0, "GiB": 1073.741824}
    return round(val * mult.get(unit, 1.0), 3)


used_mem = raw_mem.split("/")[0]
print(json.dumps({
    "model": model, "model_digest": digest,
    "cold_latency_seconds": to_float(cold),
    "warm_latency_seconds": to_float(warm),
    "total_benchmark_seconds": to_float(total),
    "peak_rss_mb": parse_size_mb(used_mem),
    "peak_cpu_percent": to_float(raw_cpu.strip().rstrip("%")),
    "swap_before_mb": to_float(swap_before),
    "swap_peak_mb": to_float(swap_peak),
    "swap_after_mb": to_float(swap_after),
    "oom_occurred": {"true": True, "false": False}.get(oom_flag),
    "keep_alive_policy": keep_alive,
}, indent=2))
PYEOF
  cat "$BASE_DIR/resources-$safe.json"

  echo "=== здоровье HELM после кандидата (поведенчески) ==="
  local health_after_core health_after_pg degraded="false"
  health_after_core=$(check_helm_core_healthz)
  health_after_pg=$(check_postgres_query)
  echo "  helm-core: до=$health_before_core после=$health_after_core"
  echo "  postgres:  до=$health_before_pg после=$health_after_pg"
  if [ "$health_before_core" = "OK" ] && [ "$health_after_core" != "OK" ]; then degraded="true"; fi
  if [ "$health_before_pg" = "OK" ] && [ "$health_after_pg" != "OK" ]; then degraded="true"; fi
  echo "  other_services_degraded=$degraded"
  python3 -c "
import json
p = '$BASE_DIR/resources-$safe.json'
d = json.load(open(p))
d['other_services_degraded'] = $degraded
json.dump(d, open(p, 'w'), indent=2)
"
  behavioral_health_check "ПОСЛЕ $model"
  echo "=== Z2 rephrase smoke после кандидата ==="
  z2_rephrase_smoke
}

echo
echo "############ КАНДИДАТ 1/3: gemma2:2b (baseline) ############"
run_candidate "gemma2:2b"

echo
echo "############ КАНДИДАТ 2/3: qwen2.5:3b ############"
run_candidate "qwen2.5:3b"

echo
echo "############ КАНДИДАТ 3/3: qwen2.5:7b (нужен подъём лимита) ############"
AVAILABLE_MB=$(free -m | awk '/^Mem:/ {print $7}')
echo "доступно сейчас: ${AVAILABLE_MB}MiB"
if [ "$AVAILABLE_MB" -lt 6144 ]; then
  echo "::warning::доступно ${AVAILABLE_MB}MiB < 6144MiB — qwen2.5:7b пропущен, resource preflight не пройден"
else
  echo "=== временно поднимаем лимит ollama-контейнера до 8g (docker update, восстанавливается trap'ом) ==="
  sudo docker update --memory=8g --memory-swap=8g "$(OLLAMA_CID)"
  run_candidate "qwen2.5:7b"
fi

LITELLM_LOG_LINES_AFTER=$(sudo docker compose logs litellm 2>/dev/null | wc -l)
echo
echo "############ LITELLM RUNTIME EVIDENCE (best-effort, поверх structural AST-инварианта) ############"
echo "строк лога litellm: было $LITELLM_LOG_LINES_BEFORE, стало $LITELLM_LOG_LINES_AFTER"

# Ретракция владельца, найдено в #185-#187: здоровье проверялось ДО
# восстановления состояния ollama, а восстановление жило только в
# EXIT trap — сценарий "VERIFY health green → EXIT trap → ollama
# stopped" был буквально тем, что происходило. Восстановление вызывается
# явно ЗДЕСЬ, до финальной проверки; trap ниже по-прежнему стоит как
# сеть безопасности на случай обрыва прямо во время этого явного
# вызова — сама функция идемпотентна, повторный вызов ничего не портит.
echo
echo "############ ЯВНОЕ ВОССТАНОВЛЕНИЕ ПЕРЕД ФИНАЛЬНОЙ ПРОВЕРКОЙ ############"
restore_ollama_state

echo
echo "############ ЗДОРОВЬЕ ПОСЛЕ ВОССТАНОВЛЕНИЯ (то состояние, в котором сервер остаётся) ############"
behavioral_health_check "ПОСЛЕ ВОССТАНОВЛЕНИЯ"
echo "############ Z2 REPHRASE SMOKE ПОСЛЕ ВОССТАНОВЛЕНИЯ ############"
z2_rephrase_smoke

echo
echo "############ R4 GOLDEN BENCHMARK DONE (состояние ollama уже восстановлено выше) ############"
if [ -n "$CANDIDATE_FAILURES" ]; then
  echo "::error::кандидаты без валидного result.json:$CANDIDATE_FAILURES"
fi
sudo find "$BASE_DIR" -maxdepth 2 -type f -newer /opt/helm/DEPLOYED_SHA 2>/dev/null | sort
if [ -n "$CANDIDATE_FAILURES" ]; then
  exit 1
fi
