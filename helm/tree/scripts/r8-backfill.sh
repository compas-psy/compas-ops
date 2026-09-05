#!/bin/bash
# HELM v4.0 RESCUE · R8 — перенос корпуса на semantic-v2, порциями.
#
# ЗАПУСКАТЬ ТОЛЬКО через action=maintenance: скрипт пишет (новые ревизии
# разбора). Аддитивен: RAW и L1 не трогаются, прошлая ревизия остаётся
# текущей, пока новая не прошла проверку (§14.20).
#
# Порция, а не весь корпус разом. Разбор идёт локальной моделью на том
# же сервере, где живёт продукт: час непрерывной работы модели это час
# деградации всего остального. `--budget-seconds` держит один запуск в
# понятных рамках, а идемпотентность делает следующий запуск
# продолжением, а не повтором.
#
# Бюджет по умолчанию 1800 секунд; переопределяется первым аргументом.
set -uo pipefail
cd /opt/helm/compose || exit 1

BUDGET="${1:-1800}"

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "############ ДО ПОРЦИИ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.backfill --plan

echo "############ ПОРЦИЯ (бюджет ${BUDGET}с) ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.backfill \
    --budget-seconds "$BUDGET"
RC=$?

echo "############ ПОСЛЕ ПОРЦИИ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.backfill --plan

echo "############ ГОТОВО (rc=$RC) ############"
exit "$RC"
