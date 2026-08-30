#!/usr/bin/env bash
# Диагностика URLError у TelegramSender: dispatch.py логирует только тип
# исключения и HTTP-код (URLError кода не несёт вовсе), значит настоящая
# причина (DNS/connection refused/timeout/TLS) видна только изнутри
# контейнера helm-core. Read-only, ничего не меняет.
set -euo pipefail

echo "=== DNS изнутри helm-core ==="
sudo docker exec helm-helm-core-1 python3 -c "import socket; print(socket.gethostbyname('api.telegram.org'))" 2>&1 || true

echo
echo "=== HTTPS-запрос изнутри helm-core (тот же путь, что TelegramSender) ==="
sudo docker exec helm-helm-core-1 python3 -c "
import urllib.request
try:
    with urllib.request.urlopen('https://api.telegram.org', timeout=10) as r:
        print('OK status', r.status)
except Exception as e:
    print('FAIL', type(e).__name__, repr(e))
    print('reason:', getattr(e, 'reason', None))
" 2>&1 || true

echo
echo "=== Сравнение: тот же запрос изнутри hermes-gateway на хосте (для контраста) ==="
/home/helm/.hermes/hermes-agent/venv/bin/python3 -c "
import urllib.request
try:
    with urllib.request.urlopen('https://api.telegram.org', timeout=10) as r:
        print('OK status', r.status)
except Exception as e:
    print('FAIL', type(e).__name__, repr(e))
" 2>&1 || true

echo
echo "=== Сетевой режим helm-core в docker-compose ==="
grep -n "network_mode\|networks:" -A3 /opt/helm/compose/docker-compose.yml | head -40
