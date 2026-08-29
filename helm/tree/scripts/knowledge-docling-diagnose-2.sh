#!/bin/bash
# Полный traceback "Could not import module 'AutoImageProcessor'" —
# нашли похожие issue на GitHub с разными причинами (несовместимость
# transformers/torchvision, отсутствие системного _lzma) — смотрим
# РЕАЛЬНЫЙ traceback в нашем контейнере, не гадаем по чужому issue.
set -euo pipefail

cd /opt/helm/compose

echo "===== Версии transformers/torch/torchvision ====="
sudo docker compose exec -T helm-knowledge-worker pip show transformers torch torchvision 2>&1 | grep -E "^Name|^Version|WARNING"

echo
echo "===== _lzma доступен? ====="
sudo docker compose exec -T helm-knowledge-worker python3 -c "import lzma; print('lzma OK')" 2>&1

echo
echo "===== Полный traceback AutoImageProcessor ====="
sudo docker compose exec -T helm-knowledge-worker python3 -c "from transformers import AutoImageProcessor; print('AutoImageProcessor OK')" 2>&1

echo "DONE"
