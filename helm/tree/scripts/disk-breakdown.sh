#!/usr/bin/env bash
# HELM · чем именно заняты 79 из 99 гигабайт. Только чтение.
#
# ЗАЧЕМ. Замер 07.09.2026: диск 84%, при этом весь /opt — 250 МБ.
# Значит место съедено не данными, а докером и системой. Прежде чем
# что-то удалять, нужно знать что и сколько — уборка вслепую на боевом
# сервере это способ снести нужный образ.
#
# Веха D по ТЗ — переезд SignalAI на этот же сервер. С 16 ГБ свободного
# места это решение принимается вслепую.
set -uo pipefail

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. КУДА ДЕЛСЯ ДИСК ############"
df -h / | tail -1
echo
echo "— верхний уровень (без /proc и /sys):"
sudo du -sh /var /opt /home /usr /root /tmp 2>/dev/null | sort -rh

echo
echo "############ 2. ДОКЕР: ИТОГО ############"
sudo docker system df

echo
echo "############ 3. ОБРАЗЫ ПО РАЗМЕРУ ############"
sudo docker images --format '{{.Size}}\t{{.Repository}}:{{.Tag}}\t{{.CreatedSince}}' \
  | sort -rh | head -20

echo
echo "############ 4. ЧТО ИЗ НИХ НИКЕМ НЕ ИСПОЛЬЗУЕТСЯ ############"
used=$(sudo docker ps -a --format '{{.Image}}' | sort -u)
echo "— образы, на которых стоят контейнеры:"
printf '%s\n' "$used" | sed 's/^/    /'
echo
echo "— висячие (dangling) слои:"
sudo docker images -f dangling=true --format '{{.Size}}\t{{.ID}}' | sort -rh | head -10
echo "  всего висячих: $(sudo docker images -f dangling=true -q | wc -l)"

echo
echo "############ 5. ТОМА ############"
sudo docker system df -v 2>/dev/null | sed -n '/VOLUME NAME/,/^$/p' | head -20

echo
echo "############ 6. ЖУРНАЛЫ И КЭШИ ############"
echo "— journald:"
sudo journalctl --disk-usage 2>/dev/null || echo "  недоступно"
echo "— логи контейнеров:"
sudo du -sh /var/lib/docker/containers 2>/dev/null | tail -1
echo "— apt-кэш:"
sudo du -sh /var/cache/apt 2>/dev/null | tail -1
echo "— модели ollama:"
sudo du -sh /var/lib/docker/volumes/*ollama* 2>/dev/null | tail -3
echo "— точки возврата:"
sudo du -sh /opt/helm-rescue-checkpoints 2>/dev/null | tail -1
sudo ls -1 /opt/helm-rescue-checkpoints 2>/dev/null | wc -l | sed 's/^/  штук: /'
