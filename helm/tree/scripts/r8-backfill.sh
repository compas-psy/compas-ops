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
# Бюджет по умолчанию 3600 секунд; переопределяется первым аргументом.
# Замер прогона 287: восемь источников за 1520 секунд модели, в среднем
# 190 секунд на источник при разбросе от 44 до 494. Час даёт порядка
# двадцати источников — четыре порции на остаток, а не десять.
set -uo pipefail
cd /opt/helm/compose || exit 1

BUDGET="${1:-3600}"

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
