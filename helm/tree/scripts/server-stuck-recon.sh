#!/usr/bin/env bash
# HELM · почему операции на сервере встали. ТОЛЬКО ЧТЕНИЕ.
#
# Прогон 481 (приёмка) висел 31 минуту, прогон 482 встал уже на снятии
# точки возврата — шаге, который обычно занимает восемь секунд. Значит
# дело не в скрипте приёмки, а в состоянии самого сервера.
#
# Гипотеза, которую надо подтвердить или опровергнуть: отменённый прогон
# оставил висеть `docker compose exec`, и операции docker встали за ним.
set -uo pipefail

echo "############ НАГРУЗКА И ПАМЯТЬ ############"
uptime | sed 's/^/  /'
free -m | sed 's/^/  /'
echo "— диск:"
df -h / /var/lib/docker 2>/dev/null | sed 's/^/  /'

echo
echo "############ ЧТО ВИСИТ ############"
echo "— процессы docker exec:"
ps -eo pid,etimes,stat,cmd 2>/dev/null | grep -E "docker (compose )?exec" | grep -v grep \
  | awk '{printf "  pid %s, живёт %s с, %s\n", $1, $2, substr($0, index($0,$4))}' | head -10 \
  || echo "  нет"
echo "— python-процессы старше 5 минут:"
ps -eo pid,etimes,stat,cmd 2>/dev/null | awk '$2 > 300 && /python/ && !/awk/ {printf "  pid %s, живёт %s с, %s\n", $1, $2, substr($0, index($0,$4))}' | head -10 \
  || echo "  нет"
echo "— самые тяжёлые процессы:"
ps -eo pid,pcpu,pmem,etimes,cmd --sort=-pcpu 2>/dev/null | head -8 | sed 's/^/  /'

echo
echo "############ КОНТЕЙНЕРЫ ############"
sudo timeout 30 docker ps --format '  {{.Names}} {{.Status}}' 2>&1 | head -15
echo "— отвечает ли docker вообще (код возврата выше 0 = нет):"
sudo timeout 15 docker info --format '  сервер docker отвечает, контейнеров {{.Containers}}' 2>&1 | tail -2

echo
echo "############ OLLAMA ############"
sudo timeout 30 docker exec helm-ollama-1 ollama ps 2>&1 | sed 's/^/  /' | head -6
echo "— журнал ollama, последние строки:"
sudo timeout 30 docker logs --tail 8 helm-ollama-1 2>&1 | sed 's/^/  /'

echo
echo "############ HELM-CORE И ВОРКЕР ############"
for c in helm-helm-core-1 helm-helm-knowledge-worker-1; do
  echo "— $c:"
  sudo timeout 20 docker logs --tail 6 "$c" 2>&1 | sed 's/^/    /'
done
