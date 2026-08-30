#!/usr/bin/env bash
set -euo pipefail

echo "=== ufw status ==="
sudo ufw status verbose 2>&1 || echo "ufw не используется/не установлен"

echo
echo "=== iptables INPUT (первые 40 строк) ==="
sudo iptables -L INPUT -n -v --line-numbers 2>&1 | head -40

echo
echo "=== nft ruleset (если используется nftables) ==="
sudo nft list ruleset 2>&1 | head -80 || echo "nftables не используется/не установлен"

echo
echo "=== docker0 bridge subnet ==="
ip -4 addr show docker0 2>&1 || echo "нет интерфейса docker0"
