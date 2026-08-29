#!/bin/bash
# Read-only проверка ресурсов ПЕРЕД установкой парсеров (P8.5.2:
# MarkItDown/Docling/ffmpeg) на боевой сервер, где уже крутятся реальные
# Hermes/Postgres/n8n/Forgejo на 12GB VPS. Ничего не ставит и не меняет.
set -uo pipefail

echo "===== Диск ====="
df -h / /var/lib/docker 2>/dev/null

echo
echo "===== Память (свободно/занято сейчас) ====="
free -h

echo
echo "===== Топ-10 процессов по RSS ====="
ps -eo pid,rss,comm --sort=-rss | head -11

echo
echo "===== Контейнеры: лимиты и текущее потребление ====="
sudo docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}"

echo
echo "===== Версия python3 в venv Hermes (для совместимости пакетов) ====="
/home/helm/.hermes/hermes-agent/venv/bin/python3 --version 2>&1

echo
echo "===== Уже установлен ли markitdown/docling где-либо ====="
/home/helm/.hermes/hermes-agent/venv/bin/pip show markitdown docling 2>&1 | grep -E "Name|not found|WARNING" || echo "не найдены (ожидаемо)"

echo "DONE"
