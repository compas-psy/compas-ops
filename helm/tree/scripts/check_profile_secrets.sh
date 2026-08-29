#!/bin/bash
# Диагностика: длины секретных значений профиля Hermes, без вывода их
# содержимого. Использование: check_profile_secrets.sh <profile_name>
set -euo pipefail

PROFILE="${1:?usage: check_profile_secrets.sh <profile_name>}"
ENV_FILE="/home/helm/.hermes/profiles/${PROFILE}/.env"
CFG_FILE="/home/helm/.hermes/profiles/${PROFILE}/config.yaml"

for k in TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USERS; do
  v=$(grep "^${k}=" "$ENV_FILE" | cut -d= -f2-)
  echo "${k} length=${#v}"
done

python3 - "$CFG_FILE" <<'PYEOF'
import sys
import yaml

with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f)

model = cfg.get("model") or {}
print("model.default =", model.get("default"))
v = model.get("api_key") or ""
print("model.api_key length=", len(v))
PYEOF
