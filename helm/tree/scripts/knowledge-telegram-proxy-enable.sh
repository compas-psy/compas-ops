#!/usr/bin/env bash
# Открыть sing-box openrouter-proxy для Docker-моста (был только 127.0.0.1
# хоста), чтобы helm-core мог отправлять через него трафик к Telegram —
# найдено, что этот сервер не может напрямую достучаться до IP Telegram
# ни с хоста, ни из контейнера ("Network is unreachable"), тот же класс
# проблемы, что уже решался для openrouter.ai. route.final в конфиге уже
# "mieru-out" — отдельное правило для Telegram не нужно, только открыть
# сам listen-адрес.
set -euo pipefail

CONF=/etc/sing-box/openrouter-proxy.json

echo "=== ДО ==="
grep -A3 '"listen"' "$CONF" | head -5

sudo cp "$CONF" "${CONF}.bak-$(date +%s)"
sudo python3 - "$CONF" <<'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
for inbound in data["inbounds"]:
    if inbound.get("tag") == "openrouter-proxy-in":
        inbound["listen"] = "0.0.0.0"
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PYEOF

echo
echo "=== ПОСЛЕ ==="
grep -A3 '"listen"' "$CONF" | head -5

echo
echo "=== Рестарт sing-box ==="
sudo systemctl restart sing-box@openrouter-proxy.service
sleep 2
sudo systemctl status sing-box@openrouter-proxy.service --no-pager | head -10

echo
echo "=== Регресс: OpenRouter через прокси всё ещё работает? ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" -x http://127.0.0.1:18080 https://openrouter.ai/api/v1/models

echo
echo "=== Новое: Telegram через прокси с loopback хоста ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" -x http://127.0.0.1:18080 https://api.telegram.org

echo
echo "=== Новое: Telegram через прокси с адреса Docker-моста (как увидит helm-core) ==="
BRIDGE_IP=$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')
echo "bridge gateway: $BRIDGE_IP"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -x "http://${BRIDGE_IP}:18080" https://api.telegram.org
