#!/usr/bin/env bash
# HELM · как владельцу попасть в Forgejo.
#
# ПРОГОН 505 дал главное: учётка ILYA, почта admin@cmpas.ru, админ,
# 2FA нет, отправитель писем не настроен (значит «забыли пароль» по
# почте не работает). Порт службы — 127.0.0.1:3000.
#
# НЕ ХВАТИЛО ОДНОГО: как служба видна снаружи. Caddyfile по пути
# /opt/helm/compose/Caddyfile не нашёлся, grep вышел с кодом 2 и, будучи
# последней командой, уронил весь прогон — данные при этом были уже
# собраны. Ищем конфигурацию там, где она есть, и не даём разведке
# падать из-за ненайденного файла.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ ГДЕ КОНФИГУРАЦИЯ CADDY ############"
sudo find /opt/helm -maxdepth 3 -iname "Caddyfile*" 2>/dev/null | sed 's/^/  /'
echo "— том Caddy в compose:"
sudo docker compose config 2>/dev/null | grep -A 3 -i "caddy" | grep -i "source\|target\|image" | sed 's/^/    /'

echo
echo "############ ЕСТЬ ЛИ ПУБЛИЧНЫЙ МАРШРУТ НА FORGEJO ############"
sudo docker compose exec -T caddy sh -c \
  'grep -n -B2 -A6 "forgejo" /etc/caddy/Caddyfile 2>&1' | sed 's/^/  /'

echo
echo "############ ИТОГ ПО ДОСТУПУ ############"
sudo docker compose ps forgejo caddy --format '  {{.Name}}  {{.Ports}}' 2>&1
echo "(проверка только читает; ничего не меняется)"
exit 0
