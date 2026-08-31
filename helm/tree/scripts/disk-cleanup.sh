#!/bin/bash
# Безопасная уборка мусора на VPS (запрос владельца 31.08.2026). По
# итогам disk-usage-report.sh главный виновник найден: 20.8GB docker
# build cache в /var/lib/containerd, накопленного `docker compose
# build helm-core helm-knowledge-worker` на каждом deploy (это
# пересобираемые промежуточные слои, не рантайм-образы). Трогает
# только пересоздаваемое: build cache, dangling-образы, apt-кэш уже
# установленных .deb, журнал старше 14 дней (тот же ретеншн, что уже
# принят для log/ в CLAUDE.md §5.5). НЕ трогает docker volumes
# (postgres/forgejo/n8n/ollama_models — реальное состояние) и не
# трогает старые ядра (dpkg autoremove — отдельное решение, не мусор
# в строгом смысле).
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

echo '=== до уборки ==='
df -h / | tail -1

echo
echo '=== docker builder prune (build cache — пересоздаётся при следующем build) ==='
sudo docker builder prune -af

echo
echo '=== docker image prune (dangling-образы, не трогает используемые тэги) ==='
sudo docker image prune -f

echo
echo '=== apt-get clean (кэш скачанных .deb, уже установленных пакетов) ==='
sudo apt-get clean

echo
echo '=== journalctl vacuum (14 дней) ==='
sudo journalctl --vacuum-time=14d

echo
echo '=== после уборки ==='
df -h / | tail -1
