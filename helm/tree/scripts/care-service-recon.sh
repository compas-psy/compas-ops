#!/usr/bin/env bash
# HELM · почему служба заботы не убрала 41 ГБ кэша сборки. Только чтение.
#
# ЗАЧЕМ. ТЗ §25.6 прямо требует автоматической уборки кэша сборки с
# целевым объёмом 3–5 ГБ. Скрипт уборки в репозитории есть с 30.08
# (`guardian/cleanup.sh`, цель 4 ГБ). Прогон 451 нашёл на сервере 41.29 ГБ
# кэша — в десять раз больше цели. Значит уборка не выполняется.
#
# Три возможные причины, и до замера непонятно, какая из них верна:
#   1. таймера у cleanup.sh нет вовсе (в репозитории юнит только у
#      guardian.py и у бэкапа — но на сервере может быть свой);
#   2. таймер есть, но выключен или падает;
#   3. таймер работает, а падает сам скрипт — `docker builder prune
#      --keep-storage` в новых версиях docker переименован, и при
#      `set -euo pipefail` первая же ошибка обрывает весь скрипт.
#
# Печатается состояние юнитов и версия docker, ничего не меняется.
set -uo pipefail

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ 1. ТАЙМЕРЫ ############"
sudo systemctl list-timers --all 2>&1 | head -15

echo
echo "############ 2. ЮНИТЫ HELM ############"
sudo systemctl list-units --all '*helm*' --no-pager 2>&1 | head -20

echo
echo "############ 3. ЧТО ЛЕЖИТ В /opt/helm/guardian ############"
sudo ls -la /opt/helm/guardian 2>&1 | head -15

echo
echo "############ 4. ЗАПУСКАЛСЯ ЛИ cleanup.sh ############"
echo "— упоминания в журнале за 30 дней:"
sudo journalctl --since "30 days ago" 2>/dev/null | grep -c "cleanup.sh\|dangling images" \
  || echo "  0"
echo "— юнит helm-cleanup, если он есть:"
sudo systemctl status helm-cleanup.timer --no-pager 2>&1 | head -6

echo
echo "############ 5. ПОНИМАЕТ ЛИ DOCKER ФЛАГ ИЗ cleanup.sh ############"
echo "— версия docker:"
sudo docker --version
echo "— флаги builder prune:"
sudo docker builder prune --help 2>&1 | grep -i "keep-storage\|max-used-space\|reserved-space" \
  || echo "  ни одного из ожидаемых флагов не найдено"

echo
echo "############ 6. ДИСК СЕЙЧАС ############"
df -h / | tail -1
sudo docker system df
