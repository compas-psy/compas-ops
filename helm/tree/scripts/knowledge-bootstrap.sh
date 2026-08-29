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

# Владелец — helm (UID/GID хоста), тот же пользователь, что уже владеет
# /home/helm/.hermes и читает секреты хоста.
#
# НАЙДЕНО 29.08.2026 живым тестом P8.5.2, в два шага: chmod 700 (доступ
# только владельцу) не даёт контейнеру helm-knowledge-worker даже ЗАЙТИ
# в каталог — он работает под собственным UID (10002, useradd в
# Dockerfile.worker), не под хостовым helm (UID 1000). Первая попытка,
# 750 (rwx владельцу, rx группе), решила чтение raw/, но process_job()
# пишет L1 SOURCE .md в sources/ — для записи нужен ещё и w у группы.
# 770 + group_add: ["1001"] в docker-compose.yml (GID группы helm на
# хосте) — тот же паттерн, что уже используется для Docker secrets
# (F-260829-09), просто для каталога с чтением И записью, а не только
# чтением файла 640. Права ниже 770 не откатывать без этого контекста —
# воркер снова перестанет писать в Vault.
chown -R helm:helm "$VAULT"
chmod -R 770 "$VAULT"
# setgid на каталогах: новый файл/подкаталог, созданный воркером (UID
# 10002, primary group — свой собственный, не helm) наследует ГРУППУ
# helm от родителя, а не группу процесса-создателя. Без этого следующий
# читатель за пределами воркера (хостовый helm-пользователь, Obsidian
# по SFTP, restic под root — этот и так может, root обходит DAC) не
# гарантированно попадёт в нужную группу файла.
find "$VAULT" -type d -exec chmod g+s {} +

# Spool для входящих вложений Telegram/MAX (§14.5.1): "owner-only
# permissions, bounded size, atomic rename". Отдельно от /opt/helm-knowledge,
# потому что это временный буфер до SHA256+atomic move в raw/, а не Vault.
#
# P8.5.7: пишут сюда ДВА разных процесса под ДВУМЯ разными UID — Hermes
# (хостовый процесс, UID helm) для Telegram, и контейнер helm-core (UID
# 10001, чужой) для MAX (`/hooks/max` сам скачивает вложение и кладёт в
# spool — см. helm_core/knowledge/chat_intake.py). Буквальный chmod 700
# здесь означал бы, что helm-core физически не может писать — тот же
# паттерн, что уже понадобился для $VAULT (770 + setgid + group_add),
# применяем и здесь, а не оставляем 700 «для owner-only» дословно: смысл
# owner-only — «недоступно посторонним процессам на хосте», не «доступно
# ровно одному UID» — вложение всё равно физически покидает spool только
# после atomic move в $VAULT/raw/<domain>/, тот же ACL-периметр.
mkdir -p "$SPOOL"
chown helm:helm "$SPOOL"
chmod 770 "$SPOOL"
chmod g+s "$SPOOL"

echo "готово:"
find "$VAULT" -maxdepth 2 -type d | sort
echo "$SPOOL"
