#!/usr/bin/env bash
# ufw (default deny incoming) уже разрешает 8642/tcp (Hermes API) с
# 172.18.0.0/16 (докер-мост compose-проекта helm) — для sing-box на 18080
# такого правила не было, пакеты с моста молча дропались на INPUT (TCP
# connect внутри helm-core висел таймаутом, не "connection refused").
# Тот же паттерн, та же подсеть.
set -euo pipefail

echo "=== ДО ==="
sudo ufw status numbered | grep -E "8642|18080" || true

sudo ufw allow from 172.18.0.0/16 to any port 18080 proto tcp comment 'sing-box openrouter-proxy - docker most helm'

echo
echo "=== ПОСЛЕ ==="
sudo ufw status numbered | grep -E "8642|18080"

echo
echo "=== Проверка: TCP-коннект изнутри helm-core теперь проходит? ==="
sudo docker exec helm-helm-core-1 python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(('host.docker.internal', 18080))
    print('TCP OK')
except Exception as e:
    print('TCP FAIL', repr(e))
"

echo
echo "=== И полноценный HTTPS до Telegram через прокси изнутри контейнера ==="
sudo docker exec helm-helm-core-1 python3 -c "
import urllib.request
try:
    with urllib.request.urlopen('https://api.telegram.org', timeout=10) as r:
        print('OK status', r.status)
except Exception as e:
    print('FAIL', type(e).__name__, repr(e))
"
