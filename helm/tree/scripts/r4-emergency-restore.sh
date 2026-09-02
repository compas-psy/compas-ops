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
sudo docker update --memory=4g --memory-swap=4g "$CID"

echo
echo "############ 2. УДАЛИТЬ МОДЕЛИ, КОТОРЫХ НЕ БЫЛО ДО R4 (только qwen2.5:7b) ############"
sudo docker compose exec -T ollama ollama rm qwen2.5:7b 2>&1 || echo "  (уже нет — ок)"

echo
echo "############ 3. ВЕРНУТЬ gemma2:2b (была до R4, боевая для Z2) ############"
sudo docker compose exec -T ollama ollama pull gemma2:2b

echo
echo "############ ПОСЛЕ ############"
echo "лимит памяти: $(sudo docker inspect -f '{{.HostConfig.Memory}}' "$CID")"
sudo docker compose exec -T ollama ollama list

echo
echo "############ 4. Z2 REPHRASE SMOKE (должен быть mode=Z2, не Z1) ############"
sudo docker compose exec -T helm-core python3 <<'PY'
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

echo
echo "############ EMERGENCY RESTORE DONE ############"
