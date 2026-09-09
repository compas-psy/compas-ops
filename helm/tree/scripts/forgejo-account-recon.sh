#!/usr/bin/env bash
# HELM · какие учётки заведены в Forgejo и чем восстанавливать доступ.
#
# ЗАЧЕМ. Владелец не помнит пароль и почту от Forgejo. Пароль агент не
# заводит и не пересылает (CLAUDE.md §5.4) — но имя учётки, её почту и
# способ входа выяснить можно и нужно: без них владелец не может даже
# начать восстановление.
#
# ЧТО ЗДЕСЬ ЕСТЬ И ЧЕГО НЕТ. Печатаются имена, почты, признак админа и
# активности; настроен ли отправитель писем (само наличие, без пароля
# SMTP); как служба видна снаружи. НЕ печатаются: пароли, хэши, токены,
# содержимое секретов. Только чтение, ничего не меняется.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ УЧЁТКИ ############"
sudo docker compose exec -T -u git forgejo sh -c 'forgejo admin user list 2>&1' | sed 's/^/  /'

echo
echo "############ ВОССТАНОВЛЕНИЕ ПО ПИСЬМУ ############"
# Работает ли «забыли пароль» — зависит от того, настроен ли отправитель.
echo "— секция [mailer] в конфигурации:"
sudo docker compose exec -T -u git forgejo sh -c \
  'awk "/^\[mailer\]/,/^\[/" /data/gitea/conf/app.ini 2>/dev/null \
   | grep -v -i "passwd\|password"' | sed 's/^/    /'
echo "  (если пусто или ENABLED=false — письмо не уйдёт, восстанавливать через консоль)"

echo
echo "############ КАК ВОЙТИ ############"
echo "— порты службы:"
sudo docker compose ps forgejo --format '  {{.Name}}  {{.Ports}}' 2>&1
echo "— маршрут в Caddy:"
sudo grep -n "git\." /opt/helm/compose/Caddyfile 2>/dev/null | head -5 | sed 's/^/    /'
