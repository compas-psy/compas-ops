#!/bin/bash
# HELM v4.0 RESCUE · R4 — живой прогон golden-бенчмарка + сравнение
# keep_alive на реальных кандидатах (§14.18, R4 пп.3-6).
#
# Кандидаты выбраны ПОСЛЕ r4-inventory.sh (не по «уже скачана»):
#   gemma2:2b   — обязательный baseline (уже на сервере, было для Z2).
#   qwen2.5:3b  — современный 3-4B instruct, уже проверен pullable на
#                 этом VPS (тот же Z2-замер, ollama-benchmark.sh).
#   qwen2.5:7b  — «сильный» 7-8B instruct. Пробуется ТОЛЬКО потому, что
#                 inventory показал 45G диска и ~9.1G available RAM —
#                 см. r4-inventory.sh прогон. Текущий compose-лимит
#                 ollama (4GiB) реального q4 7B физически не пропустит
#                 (cgroup убьёт контейнер независимо от свободной RAM
#                 хоста) — лимит поднимается ВРЕМЕННО `docker update`,
#                 не правкой docker-compose.yml: recon не персистит
#                 конфигурацию, и следующий обычный деплой/`up` вернёт
#                 объявленные 4GiB сам, без отдельного отката.
#
# Идемпотентно: если результат кандидата уже лежит на диске — кандидат
# пропускается. Можно звать recon с этим же именем скрипта повторно,
# если один прогон не уложился в 60-минутный лимит джобы.
#
# Публикации графа здесь нет вообще — CLI (semantic_benchmark.py) не
# знает о publish_semantic_run(), только зовёт extract_window() напрямую
# (R4 п.1: «Не использовать publish_semantic_run() для owner corpus»).
set -uo pipefail
cd /opt/helm/compose

RUN_DIR=/opt/helm-state/benchmarks/r4/run1
# НАЙДЕНО живым прогоном 02.09.2026: `sudo mkdir -p` создаёт каталог
# root:root, а `docker compose exec ... > "$out"` ниже открывает файл
# ОБЫЧНЫМ пользователем helm (sudo относится только к самой команде
# docker) — редирект падал с Permission denied ДО запуска python,
# извлечение ни разу не выполнилось. chown сразу после mkdir отдаёт
# каталог текущему пользователю SSH-сессии.
sudo mkdir -p "$RUN_DIR"
sudo chown "$(id -u):$(id -g)" "$RUN_DIR"

OLLAMA_CID() { sudo docker compose ps -q ollama; }

trap '
  echo "=== восстановление лимита ollama (4g, как в docker-compose.yml) ==="
  sudo docker update --memory=4g --memory-swap=4g "$(OLLAMA_CID)" >/dev/null 2>&1 || true
  sudo docker compose stop ollama >/dev/null 2>&1 || true
' EXIT

sudo docker compose up -d ollama >/dev/null
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://127.0.0.1:11434/api/tags >/dev/null || { echo "::error::ollama API не поднялся"; exit 1; }

health_snapshot() {
  sudo docker compose ps --format "{{.Service}}: {{.Status}}"
}
echo "############ ЗДОРОВЬЕ ДО БЕНЧМАРКА ############"
BEFORE_HEALTH=$(health_snapshot)
echo "$BEFORE_HEALTH"

run_golden() {
  local model="$1" out="$2"
  sudo docker compose exec -T helm-core \
    python3 -m helm_core.knowledge.semantic_benchmark golden \
    --model "$model" --keep-alive "0" --stability-repeats 3 \
    > "$out" 2> "${out%.json}.stderr.log"
}

# Латентность/RAM одного и того же кейса под keep_alive=0 (production
# сейчас) против keep_alive=5m (кандидат в production policy) — R4 п.6.
# Пять повторов подряд: первый обязательно холодный (веса ещё не в
# памяти), 2-5 — тёплые при keep_alive=5m и снова холодные каждый раз
# при keep_alive=0 (веса выгружаются между вызовами).
run_keepalive_probe() {
  local model="$1" out="$2"
  # НАЙДЕНО живым прогоном 02.09.2026: с keep_alive=0 gemma2:2b каждый
  # раз перезагружается с диска — 20 вызовов подряд занимают несколько
  # минут. Раньше весь вывод уходил ТОЛЬКО в файл (`> "$out"`), и SSH-
  # сессия несколько минут не видела ни байта — соединение рвалось как
  # «простаивающее» (client_loop: send disconnect: Broken pipe), хотя
  # работа шла. `tee` держит поток в терминале живым и одновременно
  # пишет тот же текст в файл — второй `cat` после вызова не нужен.
  {
    for ka in 0 5m; do
      echo "--- keep_alive=$ka ---"
      for i in 1 2 3 4 5; do
        t0=$(date +%s.%N)
        sudo docker compose exec -T helm-core \
          python3 -m helm_core.knowledge.semantic_benchmark golden \
          --model "$model" --keep-alive "$ka" --stability-repeats 1 \
          --case doctor_visit > /dev/null 2>> "${out%.json}.stderr.log"
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
  local golden_out="$RUN_DIR/golden-$safe.json"
  local keepalive_out="$RUN_DIR/keepalive-$safe.log"

  if [ -f "$golden_out" ] && [ -f "$keepalive_out" ]; then
    echo "=== $model: уже сделан (результаты на диске) — пропуск ==="
    return 0
  fi

  echo "=========================================="
  echo "=== кандидат: $model ==="
  echo "=========================================="
  echo "=== ollama pull ==="
  time sudo docker compose exec -T ollama ollama pull "$model"

  if [ ! -f "$golden_out" ]; then
    echo "=== golden benchmark (keep_alive=0, качество) ==="
    time run_golden "$model" "$golden_out"
    echo "-- итог (schema_stats/metrics верхнего уровня) --"
    python3 -c "
import json
d = json.load(open('$golden_out'))
print('schema_stats:', d['schema_stats'])
print('total_material_hallucinations:', d['metrics']['total_material_hallucinations'])
print('safety_case_hallucinations:', d['metrics']['safety_case_hallucinations'])
print('entity_precision/recall:', d['metrics']['entity_precision'], d['metrics']['entity_recall'])
print('atom_precision/recall:', d['metrics']['atom_precision'], d['metrics']['atom_recall'])
print('relation_precision/recall:', d['metrics']['relation_precision'], d['metrics']['relation_recall'])
print('p50/p95 latency:', d['p50_latency'], d['p95_latency'])
" || echo "::error::не удалось разобрать $golden_out — см. ${golden_out%.json}.stderr.log"
  fi

  echo "=== RSS сразу после golden ==="
  sudo docker stats --no-stream "$(OLLAMA_CID)" --format "{{.MemUsage}}"

  if [ ! -f "$keepalive_out" ]; then
    echo "=== keep_alive: cold(0) vs warm(5m) на одном кейсе x5 ==="
    run_keepalive_probe "$model" "$keepalive_out"
  fi

  echo "=== здоровье HELM после кандидата ==="
  health_snapshot

  echo "=== ollama rm (диск для следующего кандидата) ==="
  sudo docker compose exec -T ollama ollama rm "$model" || true
}

echo
echo "############ КАНДИДАТ 1/3: gemma2:2b (baseline) ############"
run_candidate "gemma2:2b"

echo
echo "############ КАНДИДАТ 2/3: qwen2.5:3b ############"
run_candidate "qwen2.5:3b"

echo
echo "############ КАНДИДАТ 3/3: qwen2.5:7b (нужен подъём лимита) ############"
# Живой повторный замер прямо перед попыткой — снимок из r4-inventory.sh
# сделан отдельным прогоном раньше и мог устареть. 6GiB — 8GiB потолок
# ollama плюс запас для postgres/litellm/n8n/forgejo/helm-core/helm-embed,
# уже занимающих место на этом же хосте; ниже — 7B не пробуем, это и есть
# сам resource preflight (R4 п.3), а не догадка.
AVAILABLE_MB=$(free -m | awk '/^Mem:/ {print $7}')
echo "доступно сейчас: ${AVAILABLE_MB}MiB"
if [ "$AVAILABLE_MB" -lt 6144 ]; then
  echo "::warning::доступно ${AVAILABLE_MB}MiB < 6144MiB — qwen2.5:7b пропущен, resource preflight не пройден"
else
  echo "=== временно поднимаем лимит ollama-контейнера до 8g (docker update, не persisted) ==="
  sudo docker update --memory=8g --memory-swap=8g "$(OLLAMA_CID)"
  run_candidate "qwen2.5:7b"
fi

echo
echo "############ ЗДОРОВЬЕ ПОСЛЕ ВСЕХ КАНДИДАТОВ ############"
AFTER_HEALTH=$(health_snapshot)
echo "$AFTER_HEALTH"
if [ "$BEFORE_HEALTH" != "$AFTER_HEALTH" ]; then
  echo "::warning::статус сервисов изменился за время бенчмарка — сверить вручную, не считать автоматически деградацией (мог быть плановый healthcheck-переход)"
fi

echo
echo "############ R4 GOLDEN BENCHMARK DONE ############"
ls -la "$RUN_DIR"

# `set -uo pipefail` без `-e` намеренно (нужны `|| true`/`|| echo` по
# ходу скрипта) — но это значит, что джоба зелёная, даже если каждый
# кандидат молча ничего не записал (найдено живым прогоном 02.09.2026:
# ровно так и было). Явная проверка на выходе — единственное, что не
# даёт "recon success" означать "результат есть".
missing=0
for f in golden-gemma2_2b.json golden-qwen2_5_3b.json; do
  [ -s "$RUN_DIR/$f" ] || { echo "::error::нет результата: $RUN_DIR/$f"; missing=$((missing + 1)); }
done
if [ "$missing" -gt 0 ]; then
  echo "::error::$missing обязательных результатов отсутствует — R4 GOLDEN BENCHMARK FAIL"
  exit 1
fi
