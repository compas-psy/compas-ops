#!/bin/bash
# Каталоги HELM Knowledge (ТЗ §14.2, §14.5.1, v3.4). Идемпотентен —
# повторный запуск ничего не портит (mkdir -p).
#
# НЕ коммитится и не зеркалится в Forgejo/GitHub (§14.2) — это runtime/
# private data, защищённые ACL + encrypted restic backup, а не код.
#
# Запуск: sudo bash /tmp/knowledge-bootstrap.sh
set -euo pipefail

VAULT=/opt/helm-knowledge
SPOOL=/opt/helm-state/knowledge-spool

mkdir -p \
  "$VAULT/inbox" \
  "$VAULT/raw/personal" \
  "$VAULT/raw/health" \
  "$VAULT/raw/simpas" \
  "$VAULT/raw/psychology" \
  "$VAULT/raw/ventures" \
  "$VAULT/raw/engineering" \
  "$VAULT/sources" \
  "$VAULT/concepts" \
  "$VAULT/entities" \
  "$VAULT/meetings" \
  "$VAULT/decisions" \
  "$VAULT/projects" \
  "$VAULT/research" \
  "$VAULT/archive" \
  "$VAULT/derived/graphify"

# owner-only: содержит health/personal/client_restricted материалы (§14.15).
# Владелец процесса — helm, тот же пользователь, что уже владеет
# /home/helm/.hermes и читает секреты хоста.
chown -R helm:helm "$VAULT"
chmod -R 700 "$VAULT"

# Spool для входящих вложений Telegram/MAX (§14.5.1): "owner-only
# permissions, bounded size, atomic rename". Отдельно от /opt/helm-knowledge,
# потому что это временный буфер до SHA256+atomic move в raw/, а не Vault.
mkdir -p "$SPOOL"
chown helm:helm "$SPOOL"
chmod 700 "$SPOOL"

echo "готово:"
find "$VAULT" -maxdepth 2 -type d | sort
echo "$SPOOL"
