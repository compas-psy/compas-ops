#!/bin/bash
# HELM v4.0 RESCUE · R8 — сколько работы в переносе корпуса.
#
# action=recon: только считает. Модель не запускается, база не меняется,
# транзакция закрывается откатом.
#
# Считает тем же условием готовности и той же выборкой, что и сам
# перенос: план, посчитанный отдельной логикой, обещал бы не то, что
# произойдёт.
#
# Наружу — числа и домены. Ни имён файлов, ни текста (§5.2 CLAUDE.md).
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "############ ОСТАТОК РАБОТЫ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.backfill --plan
RC=$?
echo "############ ГОТОВО (rc=$RC) ############"
exit "$RC"
