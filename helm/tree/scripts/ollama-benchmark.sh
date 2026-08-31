#!/bin/bash
# Живой замер кандидатов Z2 (§14.12 "опциональный локальный генератор",
# docs/KNOWLEDGE_MODELS.md) — реальный бенчмарк на живом VPS, не выбор
# по репутации (§33). Меряет RAM, латентность (холодную — модель ещё не
# в памяти, и тёплую — keep_alive держит её недолго) и печатает реальный
# сгенерированный русский текст для субъективной оценки: как есть
# (только рефраз) и с персональным стилем владельца (style.py) — этот же
# прогон закрывает и вопрос модели, и вопрос "читается ли стиль".
#
# Кандидаты — небольшие мультиязычные instruct-модели, реалистичные для
# сервера на 11GiB общей RAM с уже занятыми Postgres/helm-core/
# helm-embed/n8n/forgejo: qwen2.5:3b, gemma2:2b, llama3.2:3b (все q4 по
# умолчанию в библиотеке Ollama). 7B+ сознательно не пробуется в этом
# заходе — если ни один из трёх не пройдёт гейт, это не повод сразу
# хвататься за более тяжёлые модели, а повод зафиксировать это честно
# (спека прямо разрешает "Z2 остаётся выключенным навсегда").
#
# Ничего не остаётся резидентным после прогона: OLLAMA_KEEP_ALIVE=0 в
# docker-compose.yml выгружает веса сразу после каждого ответа, `ollama
# rm` в конце каждого кандидата освобождает диск, `docker compose stop
# ollama` в конце — контейнер не входит в обычный `deploy`'s up -d.
set -euo pipefail
cd /opt/helm/compose
trap 'sudo docker compose stop ollama' EXIT

sudo docker compose up -d ollama
echo "=== ждём готовности API ==="
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://127.0.0.1:11434/api/tags >/dev/null || { echo "::error::ollama API не поднялся"; exit 1; }

STYLE_PROMPT=$(sudo docker compose exec -T helm-core python3 -c "
from helm_core.knowledge.style import OWNER_STYLE_PROMPT
print(OWNER_STYLE_PROMPT)
")

# Один факт из реального домена (не синтетика — это текст, не голос,
# субъективное качество на нём проверяемо, в отличие от espeak-ng в
# фазах GigaAM) + вопрос, которым Z0/Z1 реально отвечали бы этой
# цитатой владельцу.
EVIDENCE="Схема — это устойчивый паттерн мышления и поведения, сформированный в детстве."
QUESTION="что такое схема?"

run_case () {
  local model="$1" system="$2" label="$3" keep_alive="$4"
  local prompt="Вопрос: ${QUESTION}
Факт: ${EVIDENCE}

Перефразируй факт как прямой ответ на вопрос."
  local payload
  payload=$(python3 -c "
import json, sys
system = sys.argv[1]
prompt = sys.argv[2]
keep_alive = sys.argv[3]
body = {'model': sys.argv[4], 'prompt': prompt, 'stream': False, 'keep_alive': keep_alive}
if system:
    body['system'] = system
print(json.dumps(body))
" "$system" "$prompt" "$keep_alive" "$model")
  local t0 t1
  t0=$(python3 -c "import time; print(time.time())")
  local response
  response=$(curl -sf http://127.0.0.1:11434/api/generate -d "$payload")
  t1=$(python3 -c "import time; print(time.time())")
  echo "--- $label (keep_alive=$keep_alive) ---"
  python3 -c "print('elapsed_seconds:', round($t1 - $t0, 2))"
  echo "$response" | python3 -c "import json,sys; print('text:', repr(json.load(sys.stdin)['response']))"
}

for model in qwen2.5:3b gemma2:2b llama3.2:3b; do
  echo "=========================================="
  echo "=== модель: $model ==="
  echo "=========================================="
  echo "=== ollama pull ==="
  time sudo docker compose exec -T ollama ollama pull "$model"

  # 1) keep_alive=30s держит веса в памяти после ответа — эта латентность
  #    включает загрузку с диска, "холодный" случай.
  run_case "$model" "" "без стиля, холодный старт (загрузка+генерация)" "30s"
  # 2) модель ещё тёплая (< 30с прошло) — та же генерация без повторной
  #    загрузки, "тёплая" латентность; keep_alive=0 выгружает веса сразу
  #    после ЭТОГО ответа, готовя чистый холодный старт для шага 3.
  run_case "$model" "" "без стиля, тёплая модель (без перезагрузки)" "0"
  # 3) снова холодный старт, на этот раз со стилем — сравнимо с шагом 1
  #    по латентности, интересен сам текст (субъективная оценка стиля).
  run_case "$model" "$STYLE_PROMPT" "со стилем владельца, холодный старт" "0"

  echo "=== RSS контейнера сразу после генерации ==="
  # НАЙДЕНО живым прогоном 31.08.2026: `docker stats` (в отличие от
  # `docker compose exec/up`) не понимает имя СЕРВИСА compose — ему
  # нужен реальный ID/имя контейнера (`docker compose ps -q` его даёт
  # независимо от того, как назван сам compose-проект).
  sudo docker stats --no-stream "$(sudo docker compose ps -q ollama)" --format "{{.MemUsage}}"

  echo "=== ollama rm (освобождаем диск перед следующим кандидатом) ==="
  sudo docker compose exec -T ollama ollama rm "$model"
done
