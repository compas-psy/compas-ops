#!/usr/bin/env bash
# HELM · права на дереве Vault: кто из контейнеров куда может писать. Чтение.
set -uo pipefail
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ ДЕРЕВО VAULT ############"
sudo find /opt/helm-knowledge -maxdepth 3 -type d -printf '%M %u:%g %p\n' | sort -k3 | head -40

echo "############ КТО ЕСТЬ КТО ############"
cd /opt/helm/compose || exit 1
for svc in helm-core helm-knowledge-worker; do
  echo "-- $svc: $(sudo docker compose exec -T $svc id 2>&1 | head -1)"
  echo "   umask: $(sudo docker compose exec -T $svc sh -c 'umask' 2>&1 | head -1)"
done
echo "############ ГОТОВО ############"
