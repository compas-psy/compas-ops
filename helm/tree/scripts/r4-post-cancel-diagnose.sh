#!/bin/bash
# HELM v4.0 RESCUE · R4 — измерить, а не предположить, состояние сервера
# после отмены #187 (владелец 02.09.2026: "Не предполагать, что trap
# сработал или не сработал — измерить"). Read-only.
set -uo pipefail
cd /opt/helm/compose

echo "############ 1. OLLAMA: running? ############"
sudo docker compose ps ollama
CID=$(sudo docker compose ps -q ollama)
if [ -n "$CID" ]; then
  echo "State.Running: $(sudo docker inspect -f '{{.State.Running}}' "$CID")"
  echo "HostConfig.Memory (bytes, 0=не задан): $(sudo docker inspect -f '{{.HostConfig.Memory}}' "$CID")"
  echo "HostConfig.MemorySwap (bytes): $(sudo docker inspect -f '{{.HostConfig.MemorySwap}}' "$CID")"
  echo "State.OOMKilled: $(sudo docker inspect -f '{{.State.OOMKilled}}' "$CID")"
  echo "RestartCount: $(sudo docker inspect -f '{{.RestartCount}}' "$CID")"
else
  echo "нет контейнера ollama вообще"
fi

echo
echo "############ 2. OLLAMA: какие модели ############"
if [ -n "$CID" ] && [ "$(sudo docker inspect -f '{{.State.Running}}' "$CID" 2>/dev/null)" = "true" ]; then
  sudo docker compose exec -T ollama ollama list
else
  echo "контейнер не запущен — поднимаю временно ТОЛЬКО чтобы посмотреть список, состояние не меняю дальше"
  sudo docker compose up -d ollama >/dev/null
  for i in $(seq 1 30); do
    curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
  sudo docker compose exec -T ollama ollama list
  echo "(контейнер теперь запущен для этой проверки — решение, останавливать ли обратно, принимается ПОСЛЕ анализа, не здесь)"
fi

echo
echo "############ 3. HELM-CORE /healthz ############"
sudo docker compose exec -T helm-core python3 -c "
import urllib.request
try:
    r = urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=5)
    print('OK', r.status)
except Exception as e:
    print('FAIL', e)
"

echo
echo "############ 4. POSTGRES ############"
sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tAc "select 1" 2>&1

echo
echo "############ 5. Z2 REPHRASE SMOKE ############"
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
               original_filename="r4-post-cancel-diagnose.txt")
    s.flush()
    result = probe(s, query="что такое схема?")
    print("outcome:", result.outcome, "| mode:", result.mode)
    print("answer_text:", repr(result.answer_text))
    s.rollback()
PY

echo
echo "############ 6. ЧТО ОСТАЛОСЬ ОТ #187 НА ДИСКЕ ############"
sudo find /opt/helm-state/benchmarks/r4 -maxdepth 3 2>/dev/null | sort

echo
echo "############ 7. ВЫКАЧЕННЫЙ SHA (для сверки с 8054892/aa0c69e) ############"
sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo "отметки нет"

echo
echo "############ DIAGNOSE DONE ############"
