#!/bin/bash
# Проверка изнутри контейнера helm-core: достаёт ли он до Hermes API
# через host.docker.internal (hermes-fix-container-network.md, шаг 4).
# Запуск: sudo bash /tmp/check-hermes-reachable.sh
set -euo pipefail
cd /opt/helm/compose
docker compose exec -T helm-core python3 -c "
import urllib.request
print(urllib.request.urlopen('http://host.docker.internal:8642/health', timeout=5).status)
"
