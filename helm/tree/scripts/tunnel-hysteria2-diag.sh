#!/usr/bin/env bash
# HELM · почему hysteria2 не даёт связности. Только чтение.
#
# Повод (06.09.2026): прогон 334 переключил sing-box на hysteria2 и
# объявил успех. Успех был ложным — моя ошибка в проверке (см. ниже),
# на деле и Telegram, и OpenRouter вернули 000. Конфиг на сервере уже
# hysteria2, mieru убран, связности нет ни через тот, ни через другой.
#
# Скрипт НИЧЕГО не меняет. Он отвечает на один вопрос: где рвётся —
# в sing-box, в сети до сервера туннеля или в самом туннеле.
#
# Адрес и порт сервера туннеля берутся из конфига на месте и печатаются
# замаскированными: они часть секрета HYSTERIA2_OUTBOUND, и в репозитории
# их быть не должно (CLAUDE.md §5.4).
set -uo pipefail

CONF=/etc/sing-box/openrouter-proxy.json
UNIT=sing-box@openrouter-proxy.service

mask() { sed -E 's/([0-9]{1,3})\.([0-9]{1,3})\.[0-9]{1,3}\.[0-9]{1,3}/\1.\2.x.x/g'; }

echo "############ 1. СЛУЖБА ############"
sudo systemctl is-active "$UNIT" | sed 's/^/  is-active: /'
sudo systemctl show "$UNIT" -p NRestarts -p Result -p ExecMainStatus | sed 's/^/  /'
echo "  версия sing-box: $(sing-box version 2>/dev/null | head -1 || echo неизвестна)"

echo
echo "############ 2. КОНФИГ (без пароля) ############"
sudo python3 -c "
import json
d = json.load(open('$CONF'))
print('  route.final:', d.get('route', {}).get('final'))
for i in d.get('inbounds', []):
    print(f\"  inbound  {i.get('tag')}: {i.get('type')} {i.get('listen')}:{i.get('listen_port')}\")
for o in d.get('outbounds', []):
    extra = ''
    if o.get('type') == 'hysteria2':
        keys = sorted(k for k in o if k not in ('type', 'tag', 'password'))
        extra = ' поля: ' + ','.join(keys)
        tls = o.get('tls') or {}
        extra += f\" tls.enabled={tls.get('enabled')} insecure={tls.get('insecure')}\"
        extra += f\" obfs={'есть' if o.get('obfs') else 'нет'}\"
    print(f\"  outbound {o.get('tag')}: {o.get('type')}{extra}\")
for s in d.get('dns', {}).get('servers', []):
    print(f\"  dns {s.get('tag')}: detour={s.get('detour')}\")
" | mask

echo
echo "############ 3. ЖУРНАЛ SING-BOX ############"
sudo journalctl -u "$UNIT" --since '30 minutes ago' --no-pager 2>/dev/null \
  | tail -60 | mask | cut -c1-220 | sed 's/^/  /'

echo
echo "############ 4. СВЯЗНОСТЬ ЧЕРЕЗ ПРОКСИ ############"
# Код и статус curl берутся ПОРОЗНЬ. В прогоне 334 было
# `code=$(curl -w '%{http_code}' ... || echo 000)`: при провале curl сам
# печатает 000, и `|| echo 000` дописывал второй — получалось «000\n000»,
# что не равно «000», и проверка объявляла успех. Ошибка моя, гейт был
# бумажный.
probe() {
  local name=$1 url=$2 code rc
  code=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' -x http://127.0.0.1:18080 "$url" 2>&1)
  rc=$?
  printf '  %-12s код=%-8s curl=%s\n' "$name" "${code:-пусто}" "$rc"
}
probe telegram   https://api.telegram.org/
probe openrouter https://openrouter.ai/
probe github     https://github.com/

echo
echo "############ 5. ВЫХОД UDP НАРУЖУ ############"
# Разделяем две разные причины: «провайдер режет UDP на высоких портах»
# и «сервер туннеля не отвечает». Первое проверяется STUN-запросом на
# заведомо живой публичный сервер — обычный UDP на порт 19302. Второе —
# отдельно, ниже.
python3 - <<'PY'
import os, random, socket, struct

def stun(host, port):
    txn = bytes(random.getrandbits(8) for _ in range(12))
    packet = struct.pack('>HHI', 0x0001, 0, 0x2112A442) + txn
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    try:
        s.sendto(packet, (host, port))
        data, _ = s.recvfrom(1024)
        return f"ответ {len(data)} байт" if data[4:8] == packet[4:8] else "ответ чужой"
    except socket.timeout:
        return "молчание (5 c)"
    except OSError as exc:
        return f"ошибка сокета: {exc}"
    finally:
        s.close()

print(f"  STUN udp/19302 (google):  {stun('stun.l.google.com', 19302)}")
print(f"  DNS  udp/53    (8.8.8.8): {stun('8.8.8.8', 53)}")
PY

echo
echo "############ 6. ДО СЕРВЕРА ТУННЕЛЯ ############"
# Порт и адрес читаются из конфига. Отсутствие ответа само по себе НЕ
# доказывает блокировку: hysteria2 по замыслу молчит на мусорный пакет.
# Значимо обратное — если сокет вообще не даёт отправить пакет.
ENDPOINT=$(sudo python3 -c "
import json
d = json.load(open('$CONF'))
o = next((x for x in d.get('outbounds', []) if x.get('type') == 'hysteria2'), None)
print(f\"{o.get('server')} {o.get('server_port')}\" if o else '')
" 2>/dev/null)
read -r HY_HOST HY_PORT <<< "$ENDPOINT"
if [ -n "${HY_HOST:-}" ]; then
  echo "  цель: $(echo "$HY_HOST" | mask):$HY_PORT"
  echo -n "  udp-отправка: "
  HY_HOST="$HY_HOST" HY_PORT="$HY_PORT" python3 -c "
import os, socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(4)
try:
    s.sendto(b'\x00' * 64, (os.environ['HY_HOST'], int(os.environ['HY_PORT'])))
    print('пакет ушёл, ошибки нет', end=' / ')
    try:
        data, _ = s.recvfrom(1024); print(f'пришёл ответ {len(data)} байт')
    except socket.timeout:
        print('ответа нет (для hysteria2 это норма)')
except OSError as exc:
    print(f'ОШИБКА ОТПРАВКИ: {exc}')
"
  echo -n "  icmp: "
  ping -c 2 -W 3 "$HY_HOST" 2>&1 | tail -2 | mask | tr '\n' ' '; echo
  echo -n "  tcp/443 туда же: "
  timeout 5 bash -c "cat < /dev/null > /dev/tcp/$HY_HOST/443" 2>&1 && echo "открыт" || echo "закрыт/таймаут"
  echo "  сокеты sing-box:"
  sudo ss -uanp 2>/dev/null | grep -i sing-box | mask | sed 's/^/    /' || echo "    (нет udp-сокетов)"
else
  echo "  в конфиге нет outbound типа hysteria2"
fi

echo
echo "############ 7. ФАЙРВОЛ ############"
sudo nft list ruleset 2>/dev/null | grep -iE 'udp|drop|reject|policy' | head -25 | mask | sed 's/^/  /' \
  || echo "  nft недоступен"
echo "  --- iptables OUTPUT ---"
sudo iptables -S OUTPUT 2>/dev/null | head -15 | mask | sed 's/^/  /' || echo "  iptables недоступен"

echo
echo "############ 8. ШЛЮЗ ############"
sudo systemctl is-active hermes-gateway | sed 's/^/  is-active: /'
sudo journalctl -u hermes-gateway --since '10 minutes ago' --no-pager 2>/dev/null \
  | tail -8 | sed -E 's/bot[0-9]+:[A-Za-z0-9_-]+/bot<МАСКА>/g; s/[A-Za-z0-9_-]{32,}/<МАСКА>/g' \
  | cut -c1-200 | sed 's/^/  /'

echo "############ ГОТОВО ############"
