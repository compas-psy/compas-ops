#!/usr/bin/env bash
# HELM · почему синтез отбросил ответ про холестерин (прогон 485).
#
# Ответ пришёл как local_not_found с формулировкой «подтвердить не смог»,
# а не «данных нет» — значит модель что-то написала, а проверка на выходе
# это отклонила. Каждая проверка пишет свою строку «синтез отброшен: …».
# Читаем ровно их и то, что было рядом.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ ОТБРАКОВКИ СИНТЕЗА ЗА ЧАС ############"
sudo docker compose logs --since 60m --no-log-prefix helm-core 2>&1 \
  | grep -E "синтез отброшен|локальный синтез недоступен" | tail -30 | sed 's/^/  /'

echo
echo "############ ВЕСЬ ХВОСТ ВОКРУГ ВОПРОСА ############"
sudo docker compose logs --since 15m --no-log-prefix helm-core 2>&1 | tail -60 | sed 's/^/  /'
