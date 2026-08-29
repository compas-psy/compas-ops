#!/bin/bash
# Диагностика "PermissionError на raw/" после group_add+chmod 770 — не
# гадаем по одному источнику, смотрим ВСЁ за один заход: реальные права
# на хосте, реальные группы внутри контейнера, реальный вид каталога
# ИЗ контейнера.
set -uo pipefail

echo "===== Права на хосте ====="
ls -la /opt/helm-knowledge
echo "--- raw/ ---"
ls -la /opt/helm-knowledge/raw
echo "--- raw/engineering/ ---"
ls -la /opt/helm-knowledge/raw/engineering
stat /opt/helm-knowledge/raw/engineering

echo
echo "===== group_add в реально запущенном контейнере ====="
cd /opt/helm/compose
sudo docker compose exec -T helm-knowledge-worker id

echo
echo "===== Вид каталога ИЗНУТРИ контейнера ====="
sudo docker compose exec -T helm-knowledge-worker ls -la /opt/helm-knowledge/raw/engineering
sudo docker compose exec -T helm-knowledge-worker stat /opt/helm-knowledge/raw/engineering

echo
echo "===== Реально применённый compose-конфиг для сервиса (group_add?) ====="
sudo docker compose config helm-knowledge-worker | grep -A 5 "group_add\|volumes"

echo "DONE"
