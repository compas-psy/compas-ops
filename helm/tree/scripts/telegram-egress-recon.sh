#!/bin/bash
# HELM · почему hermes-gateway не может выйти в Telegram.
#
# action=recon: только чтение, ничего не меняет и не перезапускает.
#
# Что уже известно (прогон 329): прямой connect к api.telegram.org:443 с
# хоста НЕ открыт, а локальный прокси 127.0.0.1:18080 слушает. Значит
# сеть до Telegram есть только через прокси, а шлюз в него не ходит.
#
# Здесь проверяется ровно это и ничего сверх:
#   1. кто слушает 18080 и есть ли на хосте tun-интерфейс;
#   2. есть ли у юнита hermes-gateway переменные прокси;
#   3. РЕШАЮЩИЙ ТЕСТ: проходит ли запрос к api.telegram.org ЧЕРЕЗ прокси.
#      Если да — лечение в одной переменной окружения, а не в сети.
#
# Токен не участвует: дёргается корень api.telegram.org без метода.
# Telegram ответит 404 — любой HTTP-код доказывает, что TCP и TLS
# прошли. Значения переменных окружения печатаются только для *_PROXY и
# NO_PROXY, остальные — именами без значений (CLAUDE.md §5.4).
set -uo pipefail

echo "############ 1. ЧТО ЕСТЬ НА ХОСТЕ ############"
echo "  --- кто слушает 18080 ---"
sudo ss -lntp 2>/dev/null | grep -E ':18080' | sed 's/^/  /' || echo "  никто"
echo "  --- tun/tap-интерфейсы ---"
ip -br link 2>/dev/null | grep -Ei 'tun|tap|wg|sing' | sed 's/^/  /' || echo "  нет"
echo "  --- маршрут по умолчанию ---"
ip route show default 2>/dev/null | sed 's/^/  /'
echo "  --- юниты, похожие на туннель ---"
systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null \
  | grep -Ei 'sing|box|xray|v2ray|proxy|tun|warp|clash|hysteria' | sed 's/^/  /' \
  || echo "  ни одного"

echo
echo "############ 2. ПРОКСИ В ОКРУЖЕНИИ ШЛЮЗА ############"
echo "  --- Environment / EnvironmentFiles юнита ---"
sudo systemctl show hermes-gateway -p Environment -p EnvironmentFiles 2>/dev/null \
  | tr ' ' '\n' \
  | grep -Ei 'proxy|EnvironmentFile' | sed 's/^/  /' \
  || echo "  переменных прокси в юните НЕТ"
echo "  --- имена переменных окружения живого процесса (без значений) ---"
pid=$(systemctl show -p MainPID --value hermes-gateway 2>/dev/null)
if [ -n "${pid:-}" ] && [ "$pid" != "0" ]; then
  sudo tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null \
    | sed -E 's/=.*//' | sort | tr '\n' ' ' | fold -w 200 | sed 's/^/  /'
  echo
  echo "  --- значения ТОЛЬКО прокси-переменных живого процесса ---"
  sudo tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null \
    | grep -Ei '^(https?_proxy|no_proxy|all_proxy)=' \
    | sed -E 's#://[^@/]*@#://<УЧЁТКА-СКРЫТА>@#' | sed 's/^/  /' \
    || echo "  ни одной прокси-переменной у процесса НЕТ"
else
  echo "  MainPID не получен"
fi

echo
echo "############ 3. РЕШАЮЩИЙ ТЕСТ ############"
echo -n "  напрямую (ожидается провал):    "
curl -sS -o /dev/null -m 8 -w '%{http_code}\n' https://api.telegram.org/ 2>&1 \
  | tail -1 | sed 's/^/HTTP /' || true
echo -n "  через 127.0.0.1:18080:          "
curl -sS -o /dev/null -m 12 -w '%{http_code}\n' \
  -x http://127.0.0.1:18080 https://api.telegram.org/ 2>&1 \
  | tail -1 | sed 's/^/HTTP /' || true
echo
echo "  HTTP 404 через прокси = TCP и TLS прошли, лечение в переменной."
echo "  Провал обоих = проблема в самом туннеле, а не в шлюзе."

echo "############ ГОТОВО ############"
