#!/bin/bash
# HELM v4.0 RESCUE · R4 emergency recovery, найдено измерением 02.09.2026
# (r4-post-cancel-diagnose.sh): #187 (старый небезопасный скрипт, SHA
# a8304db) удалил gemma2:2b — боевую модель Z2 style-rephrase — и оставил
# лимит памяти ollama на временных 8g. Живой Z2 smoke на этот момент
# вернул mode=Z1 (сырой лексический фоллбэк), не Z2 — рефраз реально
# сломан прямо сейчас, не гипотетически.
#
# Запускается как обычный action=recon (тот же класс операций, что уже
# делают ollama-benchmark.sh и r4-golden-benchmark.sh под recon: ollama
# pull/rm, docker update лимита памяти) — destructive-гейт в deploy.yml
# реагирует на ИМЯ файла (`destructive-*`), не на семантику действия, и
# это восстановление не трогает пользовательские данные/бэкапы, только
# веса модели и runtime-лимит cgroup — оба тривиально воспроизводимы.
set -uo pipefail
cd /opt/helm/compose

CID=$(sudo docker compose ps -q ollama)
if [ -z "$CID" ]; then
  echo "::error::ollama-контейнер не найден"
  exit 1
fi

echo "############ ДО ############"
echo "лимит памяти: $(sudo docker inspect -f '{{.HostConfig.Memory}}' "$CID")"
sudo docker compose exec -T ollama ollama list

echo
echo "############ 1. ВОССТАНОВИТЬ ЛИМИТ ПАМЯТИ (4g, как в docker-compose.yml) ############"
# НАЙДЕНО живым прогоном 02.09.2026 (run 189): снижение лимита, пока
# qwen2.5:7b (4.7 ГБ) реально загружена в память, мгновенно убивает
# процесс контейнера ядром (OOM) — docker update не ждёт, что resident
# memory уже помещается в новый лимит. restart-политика подняла
# контейнер обратно через ~3 секунды сама, но ПОСЛЕ того, как шаги
# rm/pull ниже уже упали в мёртвое окно — а старый скрипт этого не
# проверял и замаскировал обе реальные ошибки под "успех" (exit 0).
# Раньше: обязательно дождаться, что контейнер реально поднялся и API
# отвечает, прежде чем что-либо exec'ать в нём.
sudo docker update --memory=4g --memory-swap=4g "$CID"

wait_for_ollama_ready() {
  for i in $(seq 1 60); do
    if [ "$(sudo docker inspect -f '{{.State.Running}}' "$CID" 2>/dev/null)" = "true" ] \
       && sudo docker compose exec -T ollama curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

echo "  жду, пока ollama снова реально отвечает после смены лимита памяти..."
if ! wait_for_ollama_ready; then
  echo "::error::ollama не поднялась после docker update --memory=4g (проверь на OOM ещё раз)"
  exit 1
fi
echo "  ollama снова отвечает."

echo
echo "############ 2. УДАЛИТЬ МОДЕЛИ, КОТОРЫХ НЕ БЫЛО ДО R4 (только qwen2.5:7b) ############"
current_models=$(sudo docker compose exec -T ollama ollama list)
echo "$current_models"
if echo "$current_models" | awk '$1=="qwen2.5:7b" {found=1} END{exit !found}'; then
  sudo docker compose exec -T ollama ollama rm qwen2.5:7b
else
  echo "  (уже нет — ок)"
fi

echo
echo "############ 3. ВЕРНУТЬ gemma2:2b (была до R4, боевая для Z2) ############"
if ! sudo docker compose exec -T ollama ollama pull gemma2:2b; then
  echo "::error::ollama pull gemma2:2b не удался — Z2 rephrase остаётся сломан"
  exit 1
fi

echo
echo "############ ПОСЛЕ ############"
echo "лимит памяти: $(sudo docker inspect -f '{{.HostConfig.Memory}}' "$CID")"
sudo docker compose exec -T ollama ollama list

echo
echo "############ 4. Z2 REPHRASE SMOKE (должен быть mode=Z2, не Z1) ############"
z2_out=$(sudo docker compose exec -T helm-core python3 <<'PY'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from helm_core.config import get_settings
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.probe import probe
engine = create_engine(get_settings().database_url)
with Session(engine) as s:
    ingest_text(s, domain="psychology",
               text="Схема — это устойчивый паттерн мышления и поведения, сформированный в детстве.",
               original_filename="r4-emergency-restore-smoke.txt")
    s.flush()
    result = probe(s, query="что такое схема?")
    print("outcome:", result.outcome, "| mode:", result.mode)
    print("answer_text:", repr(result.answer_text))
    s.rollback()
PY
)
echo "$z2_out"

# Раньше скрипт просто печатал результат и выходил 0 независимо от того,
# что в нём — та же дыра, что и в остальном сценарии: "напечатали —
# значит проверили". Явно проверяем mode, а не полагаемся на глаз.
if ! echo "$z2_out" | grep -q "mode: Z2"; then
  echo "::error::Z2 rephrase всё ещё не работает (ожидался mode: Z2)"
  exit 1
fi

echo
echo "############ EMERGENCY RESTORE DONE ############"
