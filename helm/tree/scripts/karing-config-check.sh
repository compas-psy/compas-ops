#!/usr/bin/env bash
# HELM · проверить шаблон клиентского конфига настоящим sing-box. Только чтение.
#
# Повод: владельцу нужен конфиг для Karing, где российский трафик идёт
# мимо туннеля. Karing — клиент на ядре sing-box, но какая именно версия
# ядра внутри, снаружи не видно. Синтаксис sing-box за последние версии
# менялся (route actions вместо inbound.sniff, новый формат dns.servers),
# и выдать конфиг «по памяти» — значит отправить владельца отлаживать
# мою догадку.
#
# На прикладном сервере стоит sing-box 1.13.19. `check` разбирает
# конфиг, ничего не запуская и не трогая рабочий openrouter-proxy.
#
# Адрес сервера туннеля и пароль в репозиторий не попадают: здесь
# заведомо непригодные значения из TEST-NET-3 (RFC 5737). Проверяется
# синтаксис, а не связность.
set -uo pipefail

WORK=$(mktemp -d) || { echo "ОШИБКА: нет временного каталога" >&2; exit 1; }
trap 'rm -rf "$WORK"' EXIT
CONF="$WORK/karing.json"

cat > "$CONF" <<'JSON'
{
  "log": { "level": "warn" },

  "dns": {
    "servers": [
      { "tag": "dns-ru",    "address": "77.88.8.8",     "detour": "direct" },
      { "tag": "dns-proxy", "address": "tls://1.1.1.1", "detour": "hysteria2-out" }
    ],
    "rules": [
      { "rule_set": ["geosite-category-ru", "geosite-category-gov-ru"], "server": "dns-ru" },
      { "domain_suffix": [".ru", ".su", ".xn--p1ai"], "server": "dns-ru" }
    ],
    "final": "dns-proxy",
    "strategy": "ipv4_only"
  },

  "inbounds": [
    {
      "type": "tun",
      "tag": "tun-in",
      "address": ["172.19.0.1/30"],
      "mtu": 1400,
      "auto_route": true,
      "strict_route": true,
      "stack": "mixed"
    }
  ],

  "outbounds": [
    {
      "type": "hysteria2",
      "tag": "hysteria2-out",
      "server": "203.0.113.10",
      "server_port": 36712,
      "password": "ПОДСТАВЬ_ПАРОЛЬ",
      "tls": { "enabled": true, "insecure": true, "server_name": "203.0.113.10" }
    },
    { "type": "direct", "tag": "direct" }
  ],

  "route": {
    "rules": [
      { "action": "sniff" },
      { "protocol": "dns", "action": "hijack-dns" },
      { "ip_is_private": true, "outbound": "direct" },
      { "domain_suffix": [".ru", ".su", ".xn--p1ai"], "outbound": "direct" },
      { "rule_set": ["geosite-category-ru", "geosite-category-gov-ru"], "outbound": "direct" },
      { "rule_set": ["geoip-ru"], "outbound": "direct" }
    ],
    "rule_set": [
      {
        "type": "remote", "tag": "geosite-category-ru", "format": "binary",
        "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ru.srs",
        "download_detour": "hysteria2-out", "update_interval": "7d"
      },
      {
        "type": "remote", "tag": "geosite-category-gov-ru", "format": "binary",
        "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-gov-ru.srs",
        "download_detour": "hysteria2-out", "update_interval": "7d"
      },
      {
        "type": "remote", "tag": "geoip-ru", "format": "binary",
        "url": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-ru.srs",
        "download_detour": "hysteria2-out", "update_interval": "7d"
      }
    ],
    "final": "hysteria2-out",
    "auto_detect_interface": true
  }
}
JSON

echo "############ ВЕРСИЯ ############"
sing-box version 2>&1 | head -2 | sed 's/^/  /'

echo
echo "############ ПРОВЕРКА ШАБЛОНА ############"
# Без sudo и без -c рабочего конфига: файл наш, во временном каталоге.
if sing-box check -c "$CONF" 2>&1 | sed 's/^/  /'; then
  echo "  ПРИНЯТ"
else
  echo "  ОТВЕРГНУТ — смотри сообщение выше"
fi

echo
echo "############ ЧТО ЕЩЁ ПОДДЕРЖИВАЕТСЯ ############"
# Отдельно проверяем спорные места: если ядро Karing старее, ему может
# не хватить route actions, а если новее — устаревшего inbound.sniff.
# Знать заранее, какая из двух форм проходит, дешевле, чем гадать.
probe_form() {
  local name=$1 body=$2 f="$WORK/probe.json"
  printf '%s' "$body" > "$f"
  if sing-box check -c "$f" >/dev/null 2>&1; then
    printf '  %-32s принимается\n' "$name"
  else
    printf '  %-32s ОТВЕРГНУТО: %s\n' "$name" "$(sing-box check -c "$f" 2>&1 | head -1)"
  fi
}
BASE_OUT='"outbounds":[{"type":"direct","tag":"direct"}]'
probe_form 'route rule action:sniff' \
  "{$BASE_OUT,\"route\":{\"rules\":[{\"action\":\"sniff\"}],\"final\":\"direct\"}}"
probe_form 'inbound sniff:true (старая форма)' \
  "{\"inbounds\":[{\"type\":\"tun\",\"tag\":\"t\",\"address\":[\"172.19.0.1/30\"],\"sniff\":true}],$BASE_OUT}"
probe_form 'dns servers старая форма' \
  "{$BASE_OUT,\"dns\":{\"servers\":[{\"tag\":\"d\",\"address\":\"8.8.8.8\",\"detour\":\"direct\"}]}}"
probe_form 'dns servers новая форма (1.12+)' \
  "{$BASE_OUT,\"dns\":{\"servers\":[{\"tag\":\"d\",\"type\":\"udp\",\"server\":\"8.8.8.8\",\"detour\":\"direct\"}]}}"

echo "############ ГОТОВО ############"
