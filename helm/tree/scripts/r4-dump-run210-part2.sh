#!/bin/bash
# HELM v4.0 RESCUE · R4.5.5 — часть 2 read-only recon.
# Первый дамп (r4-dump-run210-artifacts.sh) не влез целиком в лог
# GitHub Actions (кэп на размер лога у инструмента чтения) — начало
# файла (gemma2_2b result.json целиком, начало qwen2_5_3b result.json)
# обрезалось. Плюс: resources-*.json показали oom_occurred=true у ВСЕХ
# трёх кандидатов при плоском swap (509-511 МБ до/после) — похоже на
# устаревший State.OOMKilled, унаследованный от рестарта КОНТЕЙНЕРА
# задолго до этого прогона (docker update лимита памяти не рестартует
# контейнер и не сбрасывает флаг), а не на реальный OOM во время run
# 210. Проверяем именно это, ничего не меняя.
set -uo pipefail
cd /opt/helm/compose

BASE_DIR=/opt/helm-state/benchmarks/r4
OLLAMA_CID=$(sudo docker compose ps -q ollama)

echo "############ OOM-таймлайн ollama-контейнера (только чтение) ############"
sudo docker inspect -f '{{.State.StartedAt}}' "$OLLAMA_CID" | sed 's/^/StartedAt: /'
sudo docker inspect -f '{{.State.OOMKilled}}' "$OLLAMA_CID" | sed 's/^/OOMKilled (current): /'
sudo docker inspect -f '{{.RestartCount}}' "$OLLAMA_CID" | sed 's/^/RestartCount: /'
sudo docker inspect -f '{{.State.Running}}/{{.State.Restarting}}' "$OLLAMA_CID" | sed 's/^/Running\/Restarting: /'
sudo docker inspect -f '{{.HostConfig.Memory}}' "$OLLAMA_CID" | sed 's/^/HostConfig.Memory (bytes): /'
echo "-- run 210 стартовал 2026-09-03T05:54:22Z, закончился 2026-09-03T08:08:38Z --"
echo "-- если StartedAt контейнера РАНЬШЕ 05:54:22Z, OOMKilled не мог быть вызван ЭТИМ прогоном --"

echo
echo "############ dmesg: последние OOM-события ядра (если видны без привилегий выше sudo) ############"
sudo dmesg -T 2>/dev/null | grep -i "out of memory\|oom-kill\|killed process" | tail -20 || echo "(dmesg недоступен или пуст)"

echo
echo "############ docker events: OOM за последние 48 часов для этого контейнера ############"
sudo docker events --since 48h --until now --filter "container=$OLLAMA_CID" --filter "event=oom" 2>/dev/null || echo "(нет OOM-событий в этом окне или docker events недоступен)"

echo
echo "############ RUN210 result.json: gemma2_2b-bf9d1c7abb31112d (полностью) ############"
sudo cat "$BASE_DIR/gemma2_2b-bf9d1c7abb31112d/result.json"
