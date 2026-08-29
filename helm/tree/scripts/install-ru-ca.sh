#!/usr/bin/env bash
# Доверие корню Минцифры на сервере HELM.
#
# Зачем. Сертификат platform-api2.max.ru выдан «Russian Trusted Sub CA»
# (Минцифры). Этого корня нет в стандартном наборе Debian/Ubuntu, поэтому
# сервер отвергает соединение с MAX Bot API как самоподписанное:
# «SSL certificate problem: unable to get local issuer certificate».
# Без него не работает ни регистрация вебхука, ни отправка ответов
# владельцу — то есть канал MAX (§10) не существует.
#
# Основание: решение учредителя от 18.08.2026 доверять этому центру
# (принято для платёжного шлюза Т-Банк в cmpas.ru, scripts/install-ru-ca.sh).
# Здесь та же процедура, с одним усилением: отпечатки не печатаются для
# чтения глазами, а СВЕРЯЮТСЯ с эталонными — теми, что зафиксированы в
# отчёте того прогона (cmpas.ru, docs/ops/ru-ca.md). Расхождение
# означает, что скачалось не то, и установка прекращается.
set -euo pipefail

ROOT_URL='https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt'
SUB_URL='https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt'
DEST=/usr/local/share/ca-certificates
BUNDLE_DIR=/etc/ssl/ru-ca
BUNDLE="$BUNDLE_DIR/russian-trusted.pem"

# Эталонные отпечатки SHA-256. Проверено на сервере cmpas.ru 18.08.2026.
ROOT_FP='D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31'
SUB_FP='BB:BD:E2:10:3E:79:0B:99:9E:C6:2B:D0:3C:F6:25:A5:A2:E7:C3:16:E1:0A:FE:6A:49:0E:ED:EA:D8:B3:FD:9B'

log() { echo "[ru-ca] $*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "требуется root: sudo /opt/helm/scripts/install-ru-ca.sh" >&2
  exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

log "Скачиваю корневой и промежуточный сертификаты Минцифры."
curl -fsS --max-time 30 -o "$tmp/root.crt" "$ROOT_URL"
curl -fsS --max-time 30 -o "$tmp/sub.crt" "$SUB_URL"

check() {
  local file="$1" expected="$2" name="$3" actual
  if ! openssl x509 -in "$file" -noout >/dev/null 2>&1; then
    log "ОШИБКА: $name не является сертификатом. Ничего не установлено."
    exit 1
  fi
  actual=$(openssl x509 -in "$file" -noout -fingerprint -sha256 | cut -d= -f2)
  log "--- $name ---"
  openssl x509 -in "$file" -noout -subject -issuer -dates
  log "отпечаток: $actual"
  if [ "$actual" != "$expected" ]; then
    log "ОШИБКА: отпечаток $name не совпал с эталонным."
    log "  ожидался: $expected"
    log "  получен:  $actual"
    log "Ничего не установлено. Это либо подмена, либо центр выпустил"
    log "новый сертификат — во втором случае эталон обновляется осознанно,"
    log "отдельным решением, а не правкой на месте."
    exit 1
  fi
  log "отпечаток совпал с эталонным"
}

check "$tmp/root.crt" "$ROOT_FP" root
check "$tmp/sub.crt" "$SUB_FP" sub

log "Устанавливаю в доверенные системы хоста."
install -m 0644 "$tmp/root.crt" "$DEST/russian_trusted_root_ca.crt"
install -m 0644 "$tmp/sub.crt"  "$DEST/russian_trusted_sub_ca.crt"
update-ca-certificates

log "Складываю связку для контейнера helm-core."
mkdir -p "$BUNDLE_DIR"
# awk 1 гарантирует перевод строки между сертификатами: без него
# -----END----- склеивается с -----BEGIN----- и связка не читается целиком.
awk 1 "$tmp/root.crt" "$tmp/sub.crt" > "$BUNDLE"
chmod 0644 "$BUNDLE"
log "сертификатов в связке: $(grep -c 'BEGIN CERTIFICATE' "$BUNDLE")"

log "Проверяю MAX Bot API с хоста:"
# 401 здесь — успех: TLS проверен, сервер ответил, а без токена он и
# должен отказать. Проверяется доверие цепочке, а не доступ к API.
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 \
       https://platform-api2.max.ru/subscriptions) || {
  log "ОШИБКА: соединение с MAX по-прежнему не устанавливается."
  exit 1
}
log "MAX ответил кодом $code — цепочка проверяется, доверие установлено."
