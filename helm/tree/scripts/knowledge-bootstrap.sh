#!/bin/bash
# Каталоги HELM Knowledge (ТЗ §14.2, §14.5.1, v3.4). Идемпотентен —
# повторный запуск ничего не портит (mkdir -p).
#
# НЕ коммитится и не зеркалится в Forgejo/GitHub (§14.2) — это runtime/
# private data, защищённые ACL + encrypted restic backup, а не код.
#
# НАЙДЕНО 29.08.2026 при живом деплое: раньше этот список raw/<domain>
# был написан ДО того, как §14.15 закрепили закрытым списком в
# helm_core/models/base.py::KnowledgeDomain (10 значений), и разошёлся с
# ним — "simpas" вместо 4 отдельных simpas/*, выдуманная "psychology",
# без psy-marketing и signalai-docs. helm_core/knowledge/ingest.py строит
# raw_path буквально как raw/{domain}/<sha256>.txt, поэтому подкаталоги
# обязаны совпадать со значениями enum'а СИМВОЛЬНО, включая "/" внутри
# simpas/* (mkdir -p создаёт вложенность сам). Источник истины —
# KnowledgeDomain, при добавлении домена туда — обновить и этот список.
#
# Запуск: sudo bash /tmp/knowledge-bootstrap.sh
set -euo pipefail

VAULT=/opt/helm-knowledge
SPOOL=/opt/helm-state/knowledge-spool

mkdir -p \
  "$VAULT/inbox" \
  "$VAULT/raw/personal" \
  "$VAULT/raw/health" \
  "$VAULT/raw/simpas/company" \
  "$VAULT/raw/simpas/practice" \
  "$VAULT/raw/simpas/zapiski" \
  "$VAULT/raw/simpas/moments" \
  "$VAULT/raw/psy-marketing" \
  "$VAULT/raw/ventures" \
  "$VAULT/raw/engineering" \
  "$VAULT/raw/signalai-docs" \
  "$VAULT/raw/library" \
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
