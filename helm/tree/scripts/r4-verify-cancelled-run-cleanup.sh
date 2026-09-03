#!/bin/bash
# HELM v4.0 RESCUE · read-only проверка после отмены run 225
# (mistral:7b C2 diagnostic, keep_alive=0 методология — владелец
# распорядился прекратить эту методологию и запустить заново с
# bounded warm lifecycle). Отменённый workflow run не успел выполнить
# свой собственный блок восстановления состояния ollama (снятие
# временного лимита памяти, удаление непреэкзистентной модели) — эта
# read-only проверка смотрит, остался ли какой-то след, ничего не
# меняет сама.
set -uo pipefail
cd /opt/helm/compose

echo "=== ollama list (какие модели сейчас на диске) ==="
sudo docker compose exec -T ollama ollama list

echo "=== ollama ps (что сейчас загружено в память) ==="
sudo docker compose exec -T ollama ollama ps

echo "=== текущий memory limit ollama-контейнера ==="
sudo docker inspect -f '{{.HostConfig.Memory}}' "$(sudo docker compose ps -q ollama)"

echo "=== docker compose ps (общее состояние сервисов) ==="
sudo docker compose ps
