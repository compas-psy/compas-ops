#!/usr/bin/env bash
# HELM · активация Forgejo: миграция репозиториев тем, что уже есть.
#
# ЗАЧЕМ. Прогон 478: служба жива десять дней, админ ILYA заведён,
# скрипт миграции написан месяц назад — и репозиториев НОЛЬ. Скрипт
# выходил по sys.exit при отсутствии PAT и тем самым не делал ничего,
# включая то, для чего PAT не нужен.
#
# Что здесь происходит: публичные репозитории мигрируются по URL,
# refs сверяются с GitHub, обратное зеркало не настраивается (нечем).
# Приватные не клонируются — это будет видно в выводе поимённо.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ ДО ############"
# ГДЕ ЛЕЖАТ РЕПОЗИТОРИИ. Прогон 497 показал «/data/git/repositories:
# No such file or directory», и я записал это вторым, отдельным
# дефектом. ЭТО БЫЛО НЕВЕРНО: прогон 499 создал каталог сам, путь
# оказался правильным, проверка целостности прошла (344 refs совпали с
# GitHub). Каталога не было ровно потому, что не было ни одного
# перенесённого репозитория — Forgejo заводит его при первом. Дефект
# был один, мой: скрипт не доезжал до сервера.
#
# Вывод остаётся: он стоит две секунды и отличает «путь не тот» от
# «переносить ещё нечего» — ровно ту развилку, на которой я ошибся.
echo "— ROOT из конфигурации Forgejo:"
sudo docker compose exec -T -u git forgejo sh -c \
  'grep -is "^ROOT" /data/gitea/conf/app.ini /etc/gitea/app.ini 2>/dev/null' | sed 's/^/    /'
echo "— каталоги верхнего уровня /data:"
sudo docker compose exec -T -u git forgejo sh -c 'ls -1 /data 2>&1' | sed 's/^/    /'
echo "— где лежат bare-репозитории:"
sudo docker compose exec -T -u git forgejo sh -c \
  'find /data -maxdepth 4 -type d -name "*.git" 2>/dev/null | head -20; \
   find /data -maxdepth 3 -type d -name repositories 2>/dev/null' | sed 's/^/    /'
echo "— версия скрипта миграции на сервере:"
sudo grep -c "PAT НЕОБЯЗАТЕЛЕН" /opt/helm/scripts/forgejo-migrate.py 2>/dev/null \
  | sed 's/^/    строк «PAT НЕОБЯЗАТЕЛЕН»: /'

echo
echo "############ МИГРАЦИЯ ############"
# cmpas.ru в этом запуске НЕТ намеренно: в его истории лежит утёкший
# приватный ключ (H-260818-02), и переносить её раньше решения владельца
# об этой истории значит размножить утечку ещё в одно место. Добавляется
# отдельным запуском, когда решение принято.
sudo python3 /opt/helm/scripts/forgejo-migrate.py compas-voice zapiski signalAI-mobileApp
echo "  код возврата миграции: $?"

echo
echo "############ ПОСЛЕ ############"
sudo docker compose exec -T -u git forgejo sh -c 'ls -1 /data/git/repositories 2>&1' | sed 's/^/  /'
for org in compas-psy; do
  echo "— репозитории организации $org:"
  sudo docker compose exec -T -u git forgejo sh -c "ls -1 /data/git/repositories/$org 2>&1" | sed 's/^/    /'
done
