#!/usr/bin/env bash
# HELM · перевести sing-box с мёртвого mieru на hysteria2.
#
# Повод (06.09.2026): `mieru-out` перестал отвечать. В журнале sing-box
# «open connection to api.telegram.org:443 using outbound/mieru[mieru-out]:
# failed to read socks5 connection response», и через прокси не проходит
# НИ Telegram, НИ OpenRouter — то есть умер сам upstream, а не маршрут.
# Бот молчал именно поэтому: Telegram у этого сервера доступен только
# через туннель (`knowledge-telegram-proxy-enable.sh`, 30.08.2026).
#
# ЧТО ИМЕННО МЕНЯЕТСЯ. Только `outbounds` и `route.final`. `inbounds`
# берутся из действующего конфига и не трогаются — там listen 0.0.0.0
# на 18080, открытый ради докер-моста; конфиг, присланный целиком,
# inbounds не содержит вовсе, и подстановка его как есть оставила бы
# прокси без слушателя, то есть сделала бы хуже, чем сейчас.
#
# СЕКРЕТ НЕ ЖИВЁТ В РЕПОЗИТОРИИ (CLAUDE.md §5.4). Описание outbound
# целиком, вместе с паролем, приходит на stdin из GitHub Secret
# `HYSTERIA2_OUTBOUND`. В лог не печатается ни пароль, ни адрес: только
# тип и тег. В аргументах процесса секрет тоже не появляется — stdin, не
# командная строка.
#
# ОТКАТ АВТОМАТИЧЕСКИЙ. Прежний конфиг сохраняется рядом; если после
# рестарта проверка не проходит, возвращается он и sing-box
# перезапускается обратно. Скрипт падает с ненулевым кодом, а не
# оставляет систему в неизвестном состоянии.
set -uo pipefail

CONF=/etc/sing-box/openrouter-proxy.json
UNIT=sing-box@openrouter-proxy.service
STAMP=$(date +%Y%m%dT%H%M%SZ)
BACKUP="${CONF}.bak-${STAMP}"

fail() { echo "ОШИБКА: $*" >&2; exit 1; }

OUTBOUND=$(cat)
[ -n "${OUTBOUND//[[:space:]]/}" ] || fail "на stdin пусто — секрет HYSTERIA2_OUTBOUND не заведён или не передан"
echo "$OUTBOUND" | python3 -c 'import json,sys; json.load(sys.stdin)' \
  || fail "секрет HYSTERIA2_OUTBOUND не разбирается как JSON"

# Секрет кладётся во временный файл с правами 0600, а не передаётся
# питону на stdin: `python3 -` уже читает оттуда САМУ ПРОГРАММУ, и
# heredoc затирает поток — прогон 332 упал именно на этом
# (JSONDecodeError на первом же символе). Через argv и окружение тоже
# нельзя: и то и другое видно в `ps`. Файл живёт секунды и снимается
# ловушкой в любом исходе.
umask 077
SECRET_FILE=$(mktemp) || fail "не удалось создать временный файл"
trap 'shred -u "$SECRET_FILE" 2>/dev/null || rm -f "$SECRET_FILE"' EXIT
printf '%s' "$OUTBOUND" > "$SECRET_FILE"

echo "############ ДО ############"
sudo test -f "$CONF" || fail "нет $CONF"
echo -n "  действующий route.final: "
sudo python3 -c "import json;print(json.load(open('$CONF')).get('route',{}).get('final','(нет)'))"
echo -n "  действующие inbounds:    "
sudo python3 -c "
import json
data = json.load(open('$CONF'))
print(', '.join(f\"{i.get('tag')} → {i.get('listen')}:{i.get('listen_port')}\" for i in data.get('inbounds', [])) or '(нет)')
"
echo "  версия sing-box: $(sing-box version 2>/dev/null | head -1 || echo неизвестна)"

sudo cp -a "$CONF" "$BACKUP" || fail "не удалось сохранить резервную копию"
echo "  резервная копия: $BACKUP"

echo
echo "############ ПРАВКА ############"
sudo python3 - "$CONF" "$SECRET_FILE" <<'PYEOF'
"""Меняем только выход. Вход, dns и всё прочее остаются как были."""
import json
import sys

path, secret_path = sys.argv[1], sys.argv[2]
with open(secret_path) as handle:
    outbound = json.load(handle)
if not isinstance(outbound, dict) or "tag" not in outbound or "type" not in outbound:
    raise SystemExit("outbound обязан быть объектом с полями type и tag")

with open(path) as handle:
    data = json.load(handle)

# Убираем РОВНО тот outbound, на который сейчас указывает route.final,
# остальные оставляем как есть.
#
# И перенацеливаем ВСЕ ссылки на него. Прогон 334 этого не сделал:
# у DNS-сервера `remote` остался `detour: mieru-out`, и sing-box ушёл
# в цикл падений — «start dns/udp[remote]: outbound detour not found».
# Служба не поднялась ни разу, порт 18080 не слушался, а я объявил, что
# туннель проверен.
#
# `sing-box check` этого НЕ ловит — вопреки тому, что я написал в
# прошлой правке. Он разбирает схему; висячий detour вскрывается только
# при старте службы. Поэтому единственная настоящая проверка ниже —
# служба живёт, счётчик перезапусков не растёт, порт слушается.
dead = data.get("route", {}).get("final")
kept = [item for item in data.get("outbounds", []) if item.get("tag") != dead]
data["outbounds"] = [outbound] + [item for item in kept if item.get("tag") != outbound["tag"]]
data.setdefault("route", {})["final"] = outbound["tag"]
print(f"  убран мёртвый outbound: {dead}")
print(f"  сохранены: {', '.join(item.get('tag', '?') for item in kept) or '(нет)'}")

# Проверяем не «ссылку на убранный тег», а ЛЮБУЮ висячую ссылку.
# Проверка на слепке живого конфига показала, почему это важно: после
# прогона 334 route.final уже указывает на hysteria2-out, а сломанный
# detour ведёт на mieru-out, которого в списке нет вовсе. Правило «чиню
# ссылки на тот, кого убрал» такую не увидело бы и повторный прогон
# оставил бы sing-box падать.
alive = {item.get("tag") for item in data["outbounds"]}
for server in data.get("dns", {}).get("servers", []):
    detour = server.get("detour")
    if detour and detour not in alive:
        server["detour"] = outbound["tag"]
        print(f"  dns[{server.get('tag')}].detour: {detour} → {outbound['tag']} (висячая)")
for index, rule in enumerate(data.get("route", {}).get("rules", [])):
    target = rule.get("outbound")
    if target and target not in alive:
        rule["outbound"] = outbound["tag"]
        print(f"  route.rules[{index}].outbound: {target} → {outbound['tag']} (висячая)")

with open(path, "w") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")

print(f"  outbound заменён на {outbound['type']} / {outbound['tag']}")
print(f"  route.final = {outbound['tag']}")
PYEOF
[ $? -eq 0 ] || { sudo cp -a "$BACKUP" "$CONF"; fail "правка конфига не прошла, вернул прежний"; }

echo
echo "############ ПРОВЕРКА КОНФИГА ДО РЕСТАРТА ############"
# Через sudo и по абсолютному пути: конфиг rw------- root, а у sudo
# свой secure_path, в который /usr/local/bin может не входить. Прогон
# 333 упал здесь на «permission denied» — не на конфиге, на правах.
SING_BOX=$(command -v sing-box) || fail "sing-box не найден в PATH"
if sudo "$SING_BOX" check -c "$CONF" 2>&1 | sed 's/^/  /'; then
  echo "  конфиг валиден"
else
  sudo cp -a "$BACKUP" "$CONF"
  fail "sing-box check не принял конфиг, вернул прежний — сервис не трогался"
fi

echo
echo "############ РЕСТАРТ ############"
sudo systemctl restart "$UNIT" || { sudo cp -a "$BACKUP" "$CONF"; sudo systemctl restart "$UNIT"; fail "рестарт не удался, вернул прежний конфиг"; }
# `is-active: active` само по себе не значит ничего: в цикле падений
# systemd успевает отвечать «active» между стартом и падением — в
# прогоне 334 именно это и было. Проверяем три вещи: счётчик
# перезапусков не вырос за десять секунд, служба жива, порт слушается.
restarts_before=$(sudo systemctl show "$UNIT" -p NRestarts --value)
sleep 12
restarts_after=$(sudo systemctl show "$UNIT" -p NRestarts --value)
state=$(sudo systemctl is-active "$UNIT")
echo "  is-active: $state"
echo "  NRestarts: $restarts_before → $restarts_after"
if [ "$restarts_before" != "$restarts_after" ] || [ "$state" != "active" ]; then
  echo "  --- почему упал ---"
  sudo journalctl -u "$UNIT" --since '2 minutes ago' --no-pager 2>/dev/null \
    | grep -iE 'FATAL|ERROR' | tail -5 | sed 's/^/  /'
  sudo cp -a "$BACKUP" "$CONF"
  sudo systemctl restart "$UNIT"
  fail "sing-box не держится с новым конфигом, вернул прежний"
fi
if sudo ss -ltn 2>/dev/null | grep -q ':18080'; then
  echo "  порт 18080: слушается"
else
  sudo cp -a "$BACKUP" "$CONF"
  sudo systemctl restart "$UNIT"
  fail "служба жива, но 18080 не слушается — прокси не работает, вернул прежний"
fi

echo
echo "############ ПРОВЕРКА СВЯЗНОСТИ ЧЕРЕЗ ПРОКСИ ############"
check() {
  local name=$1 url=$2 code
  # `|| echo 000` здесь стоять НЕ ДОЛЖНО: при провале curl сам печатает
  # 000 через -w, и запасное echo дописывало второй — получалось
  # «000\n000», что не равно «000». Прогон 334 на этом объявил ложный
  # успех, отката не случилось, туннель остался нерабочим.
  code=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' -x http://127.0.0.1:18080 "$url" 2>/dev/null)
  code=${code//[!0-9]/}
  printf '  %-14s %s\n' "$name" "${code:-000}"
  [ -n "$code" ] && [ "$code" != "000" ]
}
telegram_ok=1; openrouter_ok=1
check telegram   https://api.telegram.org/ || telegram_ok=0
check openrouter https://openrouter.ai/    || openrouter_ok=0

if [ "$telegram_ok" = "1" ]; then
  echo
  echo "  Telegram через туннель отвечает. Любой код кроме 000 означает,"
  echo "  что TCP и TLS прошли; 404 на корне api.telegram.org — норма."
else
  echo
  # Конфиг НЕ откатывается: прежний upstream мёртв, а этот хотя бы
  # поднимает службу и слушает порт. Возврат к падающему конфигу —
  # строго хуже. Скрипт падает, состояние названо честно.
  echo "  Telegram через туннель НЕ отвечает."
  echo "  Служба поднята и слушает 18080 — значит рвётся дальше, в самом"
  echo "  туннеле. Конфиг оставлен как есть: прежний upstream мёртв,"
  echo "  возвращать нечего. Диагностика — tunnel-hysteria2-diag.sh."
  fail "hysteria2 не дал связности до Telegram"
fi

echo
echo "############ ШЛЮЗ ############"
# hermes-gateway ходит в Telegram НЕ через прокси-переменные (их у него
# нет вовсе), а напрямую — то есть через тот же sing-box только если у
# него прозрачный маршрут. Прежде связность у него была, значит путь
# рабочий; рестарт нужен, чтобы адаптер перестал сидеть в бэкоффе.
sudo systemctl restart hermes-gateway
sleep 10
sudo systemctl is-active hermes-gateway | sed 's/^/  is-active: /'
echo "  --- журнал шлюза за минуту ---"
sudo journalctl -u hermes-gateway --since '60 seconds ago' --no-pager 2>/dev/null \
  | tail -12 | sed -E 's/bot[0-9]+:[A-Za-z0-9_-]+/bot<ЗАМАСКИРОВАНО>/g; s/[A-Za-z0-9_-]{32,}/<ЗАМАСКИРОВАНО>/g' \
  | cut -c1-200 | sed 's/^/  /'

echo "############ ГОТОВО ############"
