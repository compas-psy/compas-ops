#!/bin/bash
# HELM v4.0 RESCUE · R4.6.A — владелец 03.09.2026: диагностика двух
# timeout qwen2.5:7b из run 210 (long_dense_window, lecture_concept —
# "извлекатель недоступен: timed out" после 3 попыток по 120с каждая).
#
# Targeted: только эти 2 кейса, только qwen2.5:7b. Сравниваем
# keep_alive=0 (production policy) vs keep_alive=5m — гипотеза: keep_
# alive=0 выгружает модель СРАЗУ после каждого вызова, и каждый
# следующий вызов платит полный cold-load (resources run 210:
# qwen2.5:7b cold=133.29с против warm=72.67с — почти вдвое), из-за
# чего cold-load + генерация на самом плотном/длинном окне вместе
# вылезают за 120с REQUEST_TIMEOUT. НЕ поднимаем REQUEST_TIMEOUT
# вслепую — сначала смотрим, снимает ли warm keep_alive проблему при
# нормальной памяти/здоровье (то, что владелец явно потребовал).
#
# OOM-counter — тот же чистый cgroup v2 memory.events (R4.5.6.4), не
# lifetime State.OOMKilled: делать вывод "OOM или просто медленно" по
# устаревшему флагу здесь означало бы повторить ту же ошибку, что и в
# run 210.
set -uo pipefail
cd /opt/helm/compose

DIAG_DIR=/opt/helm-state/benchmarks/r4/diagnostic-qwen7b-timeouts
sudo mkdir -p "$DIAG_DIR"
sudo chown "$(id -u):$(id -g)" "$DIAG_DIR"

OLLAMA_CID() { sudo docker compose ps -q ollama; }

container_memory_events_path() {
  local cid="$1" pid cg_path
  pid=$(sudo docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null)
  if [ -z "$pid" ] || [ "$pid" = "0" ]; then
    return 1
  fi
  cg_path=$(sudo awk -F: '$1=="0" {print $3}' "/proc/$pid/cgroup" 2>/dev/null)
  if [ -z "$cg_path" ]; then
    return 1
  fi
  cg_path="/sys/fs/cgroup${cg_path}/memory.events"
  if ! sudo test -r "$cg_path"; then
    return 1
  fi
  echo "$cg_path"
}

read_oom_kill_counter() {
  local path="$1" value
  value=$(sudo awk '/^oom_kill /{print $2}' "$path" 2>/dev/null)
  [ -n "$value" ] && echo "$value"
}

PREEXISTING_MODELS=$(sudo docker compose exec -T ollama ollama list | tail -n +2 | awk '{print $1}')
echo "модели до диагностики:"
echo "$PREEXISTING_MODELS" | sed 's/^/  /'

ORIGINAL_MEM_LIMIT=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$(OLLAMA_CID)")
if [ "$ORIGINAL_MEM_LIMIT" = "0" ] || [ -z "$ORIGINAL_MEM_LIMIT" ]; then
  ORIGINAL_MEM_LIMIT_HUMAN="4g"
else
  ORIGINAL_MEM_LIMIT_HUMAN="${ORIGINAL_MEM_LIMIT}b"
fi
echo "лимит памяти до диагностики: $ORIGINAL_MEM_LIMIT_HUMAN"
echo "=== временно поднимаем лимит ollama до 8g (нужно для qwen2.5:7b, как в основном бенчмарке) ==="
sudo docker update --memory=8g --memory-swap=8g "$(OLLAMA_CID)"

echo "=== ollama pull qwen2.5:7b ==="
sudo docker compose exec -T ollama ollama pull qwen2.5:7b

diag_rc=0
for policy in 0 5m; do
  echo
  echo "########## keep_alive=$policy ##########"
  mem_events_path=$(container_memory_events_path "$(OLLAMA_CID)") || mem_events_path=""
  if [ -z "$mem_events_path" ]; then
    echo "::warning::cgroup v2 memory.events недоступен — OOM-инструментовка для этого блока провалится явно"
  fi
  oom_before=""
  [ -n "$mem_events_path" ] && oom_before=$(read_oom_kill_counter "$mem_events_path")
  restart_before=$(sudo docker inspect -f '{{.RestartCount}}' "$(OLLAMA_CID)")

  sudo docker compose exec -T helm-core python3 -c "
import time

from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_extract import ExtractionFailed, WindowTruncated, extract_window

FLAGGED = ('long_dense_window', 'lecture_concept')
cases = {c.case_id: c for c in GOLDEN_CASES if c.case_id in FLAGGED}
missing = set(FLAGGED) - set(cases)
if missing:
    raise SystemExit(f'кейсы не найдены в GOLDEN_CASES: {missing}')

for case_id in FLAGGED:
    case = cases[case_id]
    t0 = time.monotonic()
    try:
        extraction = extract_window(case.text, domain=case.domain, heading_path=case.heading_path,
                                    model='qwen2.5:7b', keep_alive='$policy')
        dt = time.monotonic() - t0
        print(f'{case_id}: OK за {dt:.1f}с — entities={len(extraction.entities)} '
              f'atoms={len(extraction.atoms)} edges={len(extraction.edges)} '
              f'rejected={len(extraction.rejected)}')
    except WindowTruncated as exc:
        dt = time.monotonic() - t0
        print(f'{case_id}: TRUNCATED за {dt:.1f}с — {exc}')
    except ExtractionFailed as exc:
        dt = time.monotonic() - t0
        print(f'{case_id}: FAILED за {dt:.1f}с — {exc}')
"
  step_rc=$?
  [ "$step_rc" -ne 0 ] && diag_rc="$step_rc"

  restart_after=$(sudo docker inspect -f '{{.RestartCount}}' "$(OLLAMA_CID)")
  oom_after=""
  if [ -n "$mem_events_path" ] && sudo test -r "$mem_events_path"; then
    oom_after=$(read_oom_kill_counter "$mem_events_path")
  fi
  echo "  RestartCount: $restart_before -> $restart_after"
  echo "  oom_kill counter: ${oom_before:-n/a} -> ${oom_after:-n/a}"
  if [ "$restart_before" != "$restart_after" ]; then
    echo "  ::warning::контейнер рестартовал внутри окна keep_alive=$policy — решающее свидетельство OOM, не просто медленно"
  elif [ -n "${oom_before:-}" ] && [ -n "${oom_after:-}" ] && [ "$oom_after" -gt "$oom_before" ] 2>/dev/null; then
    echo "  ::warning::oom_kill счётчик вырос внутри окна keep_alive=$policy — реальный OOM, не просто медленно"
  fi
done

echo
echo "############ ВОССТАНОВЛЕНИЕ ИСХОДНОГО СОСТОЯНИЯ OLLAMA ############"
current_models=$(sudo docker compose exec -T ollama ollama list | tail -n +2 | awk '{print $1}')
for m in $current_models; do
  found=0
  for p in $PREEXISTING_MODELS; do
    [ "$m" = "$p" ] && found=1 && break
  done
  if [ "$found" -eq 0 ]; then
    echo "  rm $m (появилась во время диагностики)"
    sudo docker compose exec -T ollama ollama rm "$m" >/dev/null 2>&1 || true
  else
    echo "  оставляем $m (была до диагностики)"
  fi
done
echo "-- лимит памяти -> $ORIGINAL_MEM_LIMIT_HUMAN --"
sudo docker update --memory="$ORIGINAL_MEM_LIMIT_HUMAN" --memory-swap="$ORIGINAL_MEM_LIMIT_HUMAN" "$(OLLAMA_CID)"

if [ "$diag_rc" -ne 0 ]; then
  echo "::error::диагностика завершилась с кодом $diag_rc"
  exit "$diag_rc"
fi
