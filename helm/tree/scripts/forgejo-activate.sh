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
sudo docker compose exec -T -u git forgejo sh -c 'ls -1 /data/git/repositories 2>&1' | sed 's/^/  /'

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
