#!/bin/bash
# Регистрация вебхука MAX (ТЗ §10.1). Запускается на сервере от root:
#   sudo /opt/helm/scripts/max-register-webhook.sh
#
# Отдельным скриптом, а не строкой в ssh-команде, по прямой причине:
# JSON с вложенными кавычками, проходящий через PowerShell → ssh → bash,
# разваливается на любом из трёх уровней экранирования (F-260828-01,
# F-260828-02 — оба найдены на этом же сервере). Здесь кавычки видит
# только bash, и вопрос экранирования исчезает вовсе.
#
# Секрет вебхука MAX не выдаёт: его придумывает наша сторона, передаёт
# здесь при регистрации, а MAX присылает обратно в заголовке
# X-Max-Bot-Api-Secret каждого вызова — так Control Plane отличает
# настоящий вебхук от постороннего запроса на публичный адрес.
set -euo pipefail

SECRETS_DIR=/etc/helm/secrets
API=https://platform-api2.max.ru
WEBHOOK_URL=https://helm.cmpas.ru/hooks/max

for name in max_bot_token max_webhook_secret; do
  if [ ! -s "$SECRETS_DIR/$name" ]; then
    echo "нет секрета $SECRETS_DIR/$name — сначала шаг 2.1 батча" >&2
    exit 1
  fi
done

TOKEN=$(cat "$SECRETS_DIR/max_bot_token")
SECRET=$(cat "$SECRETS_DIR/max_webhook_secret")

# НАЙДЕНО на живой регистрации 29.08.2026: MAX принимает секрет только из
# ограниченного алфавита и отвергает base64 из-за символов '/' и '+'
# (HTTP 400, proto.payload). Проверяем здесь, до отправки: иначе отказ
# приходит от MAX вместе с самим секретом в теле ошибки.
if ! printf '%s' "$SECRET" | grep -qE '^[A-Za-z0-9_-]+$'; then
  echo "секрет вебхука содержит символы, которые MAX не принимает." >&2
  echo "перевыпусти его шестнадцатеричным (алфавит 0-9a-f):" >&2
  echo "  openssl rand -hex 32 | sudo tee $SECRETS_DIR/max_webhook_secret" >&2
  exit 1
fi

echo "== регистрирую вебхук $WEBHOOK_URL =="
# --data-binary @- с heredoc, а не -d '...': тело собирается здесь же и
# ни через какой внешний уровень кавычек не проходит.
HTTP=$(curl -sS -o /tmp/max-subscribe.out -w '%{http_code}' \
  -X POST "$API/subscriptions" \
  -H "Authorization: $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @- <<JSON
{"url": "$WEBHOOK_URL", "update_types": ["message_created"], "secret": "$SECRET"}
JSON
)

# НАЙДЕНО 29.08.2026: MAX возвращает присланный секрет ОТКРЫТЫМ в теле
# ошибки («Field 'secret' does not match required pattern: '<секрет>'»).
# Первая версия этого скрипта печатала ответ как есть — и секрет ушёл в
# переписку. Поэтому ответ проходит через замену: печатать чужой ответ
# дословно нельзя, если в запросе был секрет.
echo "HTTP $HTTP"
sed "s|$SECRET|<секрет скрыт>|g" /tmp/max-subscribe.out
echo
rm -f /tmp/max-subscribe.out

if [ "$HTTP" != "200" ]; then
  echo "регистрация не удалась — см. тело ответа выше" >&2
  exit 1
fi

echo
echo "== текущие подписки бота =="
# Проверка независимая: подтверждение из ответа на POST и подтверждение
# отдельным GET — разные вещи, и только второе доказывает, что подписка
# действительно записана на стороне MAX. Здесь тоже замена: в списке
# подписок MAX может вернуть секрет так же открыто, как в ошибке.
curl -sS "$API/subscriptions" -H "Authorization: $TOKEN" | sed "s|$SECRET|<секрет скрыт>|g"
echo
echo
echo "Готово. Теперь напиши боту в MAX любое сообщение и посмотри лог:"
echo "  cd /opt/helm/compose && sudo docker compose logs --tail 30 helm-core | grep hooks/max"
