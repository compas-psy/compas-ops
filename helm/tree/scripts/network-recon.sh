#!/bin/bash
# Разведка сетевой изоляции перед фиксом "контейнер не достучится до
# хост-сервиса через 127.0.0.1" (ADR-020, helm-core → Hermes API 8642).
# Read-only, ничего не меняет.
set -uo pipefail

echo '=== 1. firewall (ufw) ==='
sudo ufw status verbose 2>&1

echo
echo '=== 2. iptables INPUT policy и правила ==='
sudo iptables -L INPUT -n -v 2>&1 | head -30

echo
echo '=== 3. docker-сеть helm: подсеть и gateway ==='
docker network inspect $(docker compose -f /opt/helm/compose/docker-compose.yml ps --format '{{.Networks}}' helm-core 2>/dev/null | head -1) 2>&1 \
  | grep -A5 '"IPAM"'

echo
echo '=== 4. текущий bind Hermes API (для сравнения после фикса) ==='
sudo ss -tlnp | grep 8642

echo
echo '=== 5. docker version (host-gateway доступен с 20.10+) ==='
docker version --format '{{.Server.Version}}' 2>&1
