#!/usr/bin/env bash
# HELM · попутная проверка при обычном обращении к серверу. Только чтение.
#
# ЗАЧЕМ ОДНИМ СКРИПТОМ. Распоряжение владельца 07.09.2026, п.5: «При
# следующем обычном обращении к серверу проверь уже состоявшийся запуск
# таймера. Ждать его отдельным длинным прогоном не требуется». Здесь же
# снимается состояние очереди: обе проверки нужны на одном и том же
# обращении, и разводить их по двум прогонам значит платить дважды.
#
# ЧТО СМОТРИМ:
#   1. таймер уборки сходил ли хоть раз и когда следующий;
#   2. отметки, по которым мониторинг судит об уборке;
#   3. что видит сам Guardian;
#   4. диск и кэш сборки — держится ли цель 4 ГБ;
#   5. версия разбора и очередь.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ 1. ТАЙМЕР УБОРКИ ############"
sudo systemctl list-timers helm-cleanup.timer --all --no-pager 2>&1 | head -3
echo "— состоявшиеся запуски:"
sudo journalctl -u helm-cleanup.service --since "12 hours ago" --no-pager 2>&1 \
  | grep -c "Finished helm-cleanup" | sed 's/^/  успешных завершений: /'
sudo journalctl -u helm-cleanup.service --since "12 hours ago" --no-pager 2>&1 \
  | grep -i "ОШИБКА\|Failed" | tail -5 | sed 's/^/  /' || echo "  ошибок нет"

echo
echo "############ 2. ОТМЕТКИ ДЛЯ МОНИТОРИНГА ############"
for marker in last-cleanup last-cleanup-failed; do
  path="/var/lib/helm-guardian/$marker"
  if sudo test -e "$path"; then
    echo "  $marker: $(sudo stat -c %y "$path")"
  else
    echo "  $marker: нет"
  fi
done

echo
echo "############ 3. ЧТО ВИДИТ GUARDIAN ############"
sudo python3 /opt/helm/guardian/guardian.py 2>&1 | tail -25

echo
echo "############ 4. ДИСК И КЭШ СБОРКИ ############"
df -h / | tail -1
sudo docker system df | sed -n '1p;5p'

echo
echo "############ 5. ВЕРСИЯ РАЗБОРА И ОЧЕРЕДЬ ############"
sudo docker compose exec -T helm-core python3 -m helm_core.knowledge.acceptance_probe state
