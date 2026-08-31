#!/bin/bash
# Регистрация вебхука отдельного Knowledge-бота (v3.8 §9.0/§14.3).
# Запускается на сервере от root:
#   sudo /opt/helm/scripts/knowledge-telegram-register-webhook.sh
#
# Тот же приём, что и в max-register-webhook.sh: отдельным файлом, а не
# строкой в ssh-команде, чтобы JSON с вложенными кавычками не разваливался
# на границах PowerShell → ssh → bash (F-260828-01, F-260828-02).
#
# secret_token Telegram придумывает наша сторона и передаёт здесь при
# регистрации; Telegram присылает его обратно в заголовке
# X-Telegram-Bot-Api-Secret-Token каждого вызова — так
# hooks_knowledge_telegram.py отличает настоящий вебхук от постороннего
# запроса на публичный адрес (verify_webhook_secret).
set -euo pipefail

SECRETS_DIR=/etc/helm/secrets
API=https://api.telegram.org
WEBHOOK_URL=https://helm.cmpas.ru/hooks/knowledge-telegram

# Сервер не достаёт api.telegram.org напрямую с хоста (F-260830-03) —
# исходящий трафик к Telegram пущен через локальный туннель на
# 127.0.0.1:18080 (тот же, что HTTPS_PROXY у контейнера helm-core в
# docker-compose.yml). Без --proxy curl с этого места зависает без
# ответа и без ошибки — что и произошло на первом живом запуске
# 30.08.2026, пришлось отменять зависший workflow вручную. --max-time
# ограничивает повтор той же ошибки минутой вместо часового таймаута
# job'а.
PROXY=http://127.0.0.1:18080

for name in knowledge_telegram_bot_token knowledge_telegram_webhook_secret; do
  if [ ! -s "$SECRETS_DIR/$name" ]; then
    echo "нет секрета $SECRETS_DIR/$name — сначала выкат Actions secrets" >&2
    exit 1
  fi
done

TOKEN=$(cat "$SECRETS_DIR/knowledge_telegram_bot_token")
SECRET=$(cat "$SECRETS_DIR/knowledge_telegram_webhook_secret")

curl -sS --proxy "$PROXY" --max-time 60 -X POST "$API/bot$TOKEN/setWebhook" \
  -H "Content-Type: application/json" \
  -d "$(printf '{"url":"%s","secret_token":"%s"}' "$WEBHOOK_URL" "$SECRET")"
echo

echo "=== getWebhookInfo ==="
curl -sS --proxy "$PROXY" --max-time 60 "$API/bot$TOKEN/getWebhookInfo"
echo
