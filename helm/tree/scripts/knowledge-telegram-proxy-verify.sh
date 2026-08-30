#!/usr/bin/env bash
set -euo pipefail

echo "=== env внутри helm-core (proxy-переменные) ==="
sudo docker exec helm-helm-core-1 env | grep -i proxy || echo "НЕТ proxy-переменных вовсе"

echo
echo "=== host.docker.internal резолвится во что? ==="
sudo docker exec helm-helm-core-1 python3 -c "import socket; print(socket.gethostbyname('host.docker.internal'))" 2>&1 || true

echo
echo "=== Прямой TCP-коннект на host.docker.internal:18080 изнутри контейнера ==="
sudo docker exec helm-helm-core-1 python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(('host.docker.internal', 18080))
    print('TCP OK')
except Exception as e:
    print('TCP FAIL', repr(e))
" 2>&1 || true

echo
echo "=== Прямой TCP-коннект на 172.17.0.1:18080 изнутри контейнера (для сравнения) ==="
sudo docker exec helm-helm-core-1 python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(('172.17.0.1', 18080))
    print('TCP OK')
except Exception as e:
    print('TCP FAIL', repr(e))
" 2>&1 || true

echo
echo "=== sing-box слушает где (на хосте) ==="
sudo ss -tlnp | grep 18080 || echo "НЕ СЛУШАЕТ вовсе"
