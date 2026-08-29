#!/bin/bash
# Провижининг профилей Hermes (§11.2, §15.3 п.7, §15.4, §9.1):
#   - создаёт недостающие профили (business/engineering/health;
#     reviewer и default/chief уже существуют)
#   - генерирует в LiteLLM отдельный virtual key на профиль, scoped
#     только к его alias (§15.4: "видит только alias + свой virtual
#     key") — вместо общего litellm_master_key
#   - у всех, кроме default (=chief), обнуляет TELEGRAM_BOT_TOKEN/
#     TELEGRAM_ALLOWED_USERS в .env (§9.1: только chief имеет
#     Telegram gateway; --clone-from копирует .env целиком, включая
#     токен, который worker-профилям спекой не положен)
#
# Секреты нигде не печатаются — только длины и статус.
set -euo pipefail

HERMES=/home/helm/.local/bin/hermes
SECRETS_DIR=/etc/helm/secrets
MASTER_KEY=$(sudo cat "$SECRETS_DIR/litellm_master_key")

declare -A PROFILE_ALIAS=(
  [default]=helm-standard
  [business]=helm-standard
  [engineering]=helm-code
  [health]=helm-standard
  [reviewer]=helm-review
)

for p in business engineering health; do
  if [ ! -d "/home/helm/.hermes/profiles/$p" ]; then
    "$HERMES" profile create "$p" --clone-from default
  fi
done

for profile in "${!PROFILE_ALIAS[@]}"; do
  alias="${PROFILE_ALIAS[$profile]}"
  key_alias="hermes-${profile}"
  secret_file="$SECRETS_DIR/hermes_${profile}_litellm_key"

  response=$(curl -s -X POST http://127.0.0.1:4000/key/generate \
    -H "Authorization: Bearer ${MASTER_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"models\": [\"${alias}\"], \"key_alias\": \"${key_alias}\"}")

  key=$(python3 -c "import sys, json; print(json.load(sys.stdin)['key'])" <<<"$response")

  echo -n "$key" | sudo tee "$secret_file" > /dev/null
  sudo chown root:helm-secrets "$secret_file"
  sudo chmod 640 "$secret_file"
  echo "OK: ${secret_file} (alias=${alias}, key_len=${#key})"

  if [ "$profile" = "default" ]; then
    "$HERMES" config set model.default "$alias"
    "$HERMES" config set model.api_key "$key"
  else
    "$HERMES" -p "$profile" config set model.default "$alias"
    "$HERMES" -p "$profile" config set model.api_key "$key"
    env_file="/home/helm/.hermes/profiles/${profile}/.env"
    sed -i 's/^TELEGRAM_BOT_TOKEN=.*/TELEGRAM_BOT_TOKEN=/' "$env_file"
    sed -i 's/^TELEGRAM_ALLOWED_USERS=.*/TELEGRAM_ALLOWED_USERS=/' "$env_file"
  fi
done

echo "PROVISIONING DONE"
