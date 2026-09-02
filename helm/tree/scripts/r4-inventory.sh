#!/bin/bash
# HELM v4.0 RESCUE · R4 п.3: «Кандидатов выбирать после фактического
# inventory Ollama и RAM» — не после «уже скачана» (§14.18: «Do not
# select model because "уже скачана" или "used by Z2"»). Read-only:
# ничего не публикует, ничего не переключает, только печатает факты, на
# которых потом строится список кандидатов.
#
# ollama-контейнер не входит в обычный `up -d` (см. ollama-benchmark.sh)
# — этот скрипт поднимает его сам и гасит по выходу, тем же контрактом.
set -uo pipefail
cd /opt/helm/compose
trap 'sudo docker compose stop ollama >/dev/null 2>&1' EXIT

echo "############ 1. ЖЕЛЕЗО ############"
echo "-- vCPU --"; nproc
echo "-- RAM (МБ) --"; free -m
echo "-- диск под /var/lib/docker --"; df -h /var/lib/docker 2>/dev/null || df -h /

echo
echo "############ 2. ЧТО СЕЙЧАС ЗАПУЩЕНО ############"
sudo docker compose ps

echo
echo "############ 3. OLLAMA: ЧТО УЖЕ СКАЧАНО ############"
sudo docker compose up -d ollama >/dev/null
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 1
done
if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  sudo docker compose exec -T ollama ollama list
else
  echo "::error::ollama API не поднялся за 30с"
fi

echo
echo "############ 4. ТЕКУЩИЙ ПРЕДЕЛ ПАМЯТИ OLLAMA (compose) ############"
grep -A 4 "^  ollama:" docker-compose.yml | grep -A 2 "resources:" || echo "лимит не задан явно"

echo
echo "############ 5. RSS OLLAMA ПРЯМО СЕЙЧАС (без модели в памяти) ############"
sudo docker stats --no-stream "$(sudo docker compose ps -q ollama)" --format "{{.MemUsage}}"

echo
echo "############ 6. БАЗОВАЯ ПРОВЕРКА ЗДОРОВЬЯ (для сравнения ДО/ПОСЛЕ бенчмарка) ############"
sudo docker compose ps --format "{{.Service}}: {{.Status}}"

echo
echo "############ 7. НОВЫЙ КОД БЕНЧМАРКА УЖЕ РАЗВЁРНУТ? ############"
sudo docker compose exec -T helm-core python3 -c "
import helm_core.knowledge.semantic_benchmark as m
print('semantic_benchmark.py: OK,', len(__import__('helm_core.knowledge.semantic_benchmark_fixtures', fromlist=['GOLDEN_CASES']).GOLDEN_CASES), 'golden cases')
" 2>&1 || echo "ЕЩЁ НЕ РАЗВЁРНУТ — нужен action=deploy перед живым прогоном бенчмарка"

echo
echo "############ R4 INVENTORY DONE ############"
