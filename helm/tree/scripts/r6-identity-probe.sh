#!/bin/bash
# HELM v4.0 RESCUE · R6 — проверка перед изменением живого состояния.
#
# Владелец, 05.09.2026: «Перед изменением live derived-state сделать
# read-only проверку БЕЗ вывода имён: PERSON identities with
# member_count > 1 and canonical normalized label = 1 token. Если 0 —
# текущие данные этим дефектом не затронуты.»
#
# Ничего не пишет: транзакция закрывается откатом. Наружу уходят только
# числа — подпись личности это имя врача, а вопрос здесь количественный
# (§5.2 CLAUDE.md).
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "############ ЛИЧНОСТИ-ЛЮДИ С ОДНОСЛОВНОЙ ПОДПИСЬЮ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.entity_resolution --probe
RC=$?
echo "############ ГОТОВО (rc=$RC) ############"
exit "$RC"
