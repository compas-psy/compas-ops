#!/bin/bash
# HELM v4.0 RESCUE · R6 — пересборка производных таблиц личности.
#
# ЗАПУСКАТЬ ТОЛЬКО через action=maintenance: скрипт пишет, и точка
# возврата снимается тем же гейтом, что у выката. Через `recon` —
# нельзя: recon это диагностика.
#
# Владелец, 05.09.2026 разрешил пересобрать РОВНО три производные
# таблицы для тенанта владельца: identities, identity_members,
# resolution_candidates. Они пересчитываются из узлов, поэтому удаление
# ничего не теряет безвозвратно. Исходные nodes/mentions/provenance/
# semantic runs/sources не трогаются — это проверяет инвариант внутри
# прохода, а не обещание здесь.
#
# Нужно потому, что правила тождества изменились (однословная подпись
# PERSON больше не доказательство, алиас больше не сливает), а проход
# идемпотентен и уже отнесённый узел не пересматривает: без пересборки
# старый состав остался бы старым.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "############ ПРОВЕРКА ДО ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution --probe

echo "############ ПЕРЕСБОРКА ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution --rebuild
RC=$?

echo "############ ПРОВЕРКА ПОСЛЕ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution --probe

echo "############ ГОТОВО (rc=$RC) ############"
exit "$RC"
