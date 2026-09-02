#!/bin/bash
# Ежедневный бэкап (§26.1): PostgreSQL (все БД), Control Plane config,
# LiteLLM config, профили/state/kanban Hermes, данные Forgejo (§18.7) и
# выгрузка workflow n8n (§17.5) — в restic-репозиторий на Яндекс Диске.
# Запускается от root (helm-backup.service).
#
# Оговорка про репозитории Forgejo: копируются на живом сервисе, без
# остановки. Объекты git неизменяемы, поэтому риск ограничен одним
# случаем — push ровно в момент копирования, когда ref уже обновлён, а
# объект ещё не скопирован. Такой репозиторий восстанавливается из
# GitHub-зеркала (§18.4) или из снапшота следующего дня; останавливать
# Forgejo ради этого окна дороже, чем оно стоит.
#
# Не бэкапится: сам установленный Hermes (~/.hermes/hermes-agent —
# переустанавливается по пинованной версии из install.sh), кэши
# (audio/image/cache), временные sessions/sandboxes/pending_messages —
# это runtime-мусор, не пользовательские данные.
set -euo pipefail

SECRETS_DIR=/etc/helm/secrets
export RESTIC_REPOSITORY="rclone:yandex:helm-backup"
export RESTIC_PASSWORD_FILE="$SECRETS_DIR/restic_password"

# НАЙДЕНО 01.09.2026: два живых прогона подряд упали на одном и том же —
# webdav.yandex.ru отвечал "500 Internal Server Error" и "timeout
# awaiting response headers" на 2-3 чанка (разные хэши каждый раз, не
# один и тот же повреждённый объект), restic сдавался после ~15 минут
# повторов. Квота (1TB+ свободно), лок репозитория и сама доступность
# rclone:yandex — проверены, не причина: это Yandex WebDAV, не отвечающий
# вовремя под нагрузкой на отдельные запросы. restic вызывает `rclone
# serve restic` как бэкенд — RCLONE_* переменные окружения читает сам
# бинарь rclone независимо от способа запуска. Дефолты (timeout 5m,
# low-level-retries 10) недостаточны для этого конкретного WebDAV —
# даём больше времени на ответ и больше попыток на отдельный запрос.
export RCLONE_TIMEOUT=10m
export RCLONE_CONTIMEOUT=2m
export RCLONE_LOW_LEVEL_RETRIES=20

# НАЙДЕНО 02.09.2026 (журнал helm-backup.service + `restic snapshots`).
# Репозиторий цел и доступен: 32 снапшота, листинг отвечает за шесть
# секунд. Различаются не «рабочие и нерабочие дни», а объём выгрузки:
#
#   31.08 03:32 → 03:33  BACKUP DONE   21 секунда   инкремент
#   01.09 03:34 → 03:34  BACKUP DONE   24 секунды   инкремент
#   02.09 03:38 → 03:58  Fatal 500     20 минут     много новых блоков
#
# ПРОВЕРЕНО И НЕ ПОДТВЕРДИЛОСЬ (прогон 142, 20 минут): гипотеза «Яндекс
# ограничивает частоту запросов под всплеск». `RCLONE_TPSLIMIT=4` и
# `-o rclone.connections=2` не изменили ничего — тот же `Fatal 500` на
# той же минуте. Настройки убраны, чтобы не оставлять в скрипте ручки,
# которые ничего не лечат.
#
# ЧТО ГОВОРИТ ЛОГ НА САМОМ ДЕЛЕ. Падают не случайные запросы, а два-три
# конкретных блока, и каждый — с одной и той же формулировкой:
#
#   Post request rcat error: Put ".../data/78/78219dab...": EOF
#   Post request rcat error: Put ".../data/d3/d37d318c...": i/o timeout
#
# `rcat` — это выгрузка ПОТОКОМ, когда размер тела заранее неизвестен:
# rclone отправляет её chunked transfer encoding. Остальные блоки, для
# которых размер известен, уходят обычным PUT и проходят. То есть дело
# не в темпе и не в объёме самом по себе, а в способе отправки крупных
# блоков — а chunked PUT ровно то, на чём WebDAV Яндекса и отвечает 500.
#
# ПРОВЕРЕНО И НЕ ПОДТВЕРДИЛОСЬ (прогон 144, 20 минут):
# `RCLONE_STREAMING_UPLOAD_CUTOFF=64M` не изменил путь — в логе тот же
# `rcat`. Оставлено, потому что вреда не делает и путь буферизации всё
# равно предпочтительнее потокового, но лечением не оказалось.
export RCLONE_STREAMING_UPLOAD_CUTOFF=64M

# ЧТО ПОКАЗАЛА РАЗВЕДКА СОДЕРЖИМОГО (прогон 147). Последний успешный
# снапшот `df59d5d6` от 01.09 03:34 — это 284 файла и 4.699 МиБ. Корпус
# владельца на диске — 182 файла и 63 МБ. Крупнейшие файлы снапшота:
# `postgres-dumpall.sql` 1.6 МБ, `state.db` Hermes 577 КБ, два PDF по
# 250–290 КБ. То есть в offsite-репозитории лежат конфигурация, база и
# состояние Hermes — но НЕ корпус: он приехал на сервер уже после этого
# снапшота, и с тех пор ни один прогон не дошёл до конца.
#
# Из этого следует диагноз, которого не было видно по одним отказам:
# падают ровно те прогоны, которые впервые выгружают шестьдесят
# мегабайт. Прежние успехи за двадцать секунд — это инкременты почти без
# новых данных. Значит вопрос не «почему бэкап нестабилен», а «как
# провести через этот WebDAV одну большую первичную выгрузку».
#
# Последний доступный рычаг: размер пачки. По умолчанию restic собирает
# пачки около 16 МиБ и кладёт каждую одним PUT — на них Яндекс и
# отвечает 500. Четыре мегабайта дают вчетверо больше запросов, зато
# каждый вчетверо меньше. Если и это не поможет — дело не в настройках
# клиента, а в самом хранилище, и решение за владельцем (H-260902-01).

WORKDIR=$(mktemp -d /var/lib/helm-guardian/backup-XXXXXX)
trap 'rm -rf "$WORKDIR"' EXIT

# 1. Postgres — вся кластерная выгрузка (все БД + роли), не по одной БД:
#    §2 простота — не поддерживаем отдельный список имён БД в двух местах.
docker exec helm-postgres-1 pg_dumpall -U helm > "$WORKDIR/postgres-dumpall.sql"

# 2. Консистентные снапшоты SQLite (Hermes работает в WAL-режиме — сырое
#    копирование .db без .backup рискует захватить БД в промежуточном
#    состоянии между .db/.db-wal/.db-shm).
mkdir -p "$WORKDIR/hermes-sqlite"
sqlite3 /home/helm/.hermes/kanban.db ".backup '$WORKDIR/hermes-sqlite/kanban.db'"
sqlite3 /home/helm/.hermes/state.db ".backup '$WORKDIR/hermes-sqlite/state.db'"

# 3. Выгрузка workflow n8n (§17.5, §23: «23:30 daily backup + n8n export»).
#    Неудача выгрузки не должна валить бэкап: n8n может лежать, а
#    Postgres и Hermes при этом обязаны быть сохранены.
if ! python3 /opt/helm/scripts/n8n-workflows.py export; then
  echo "ВНИМАНИЕ: выгрузка workflow n8n не удалась, бэкап продолжается"
fi

# 4. Каталог данных Forgejo (§18.7: repos + config + attachments/PR
#    metadata). БД Forgejo отдельно не нужна — она в том же кластере
#    Postgres и уже попала в pg_dumpall выше.
#
#    Путь резолвится через docker, а не пишется константой: расположение
#    каталога volume зависит от data-root демона, и захардкоженный
#    /var/lib/docker/... молча перестал бы существовать при его смене —
#    restic пропустил бы несуществующий путь, а бэкап считался бы удачным.
FORGEJO_DATA=$(docker volume inspect -f '{{.Mountpoint}}' helm_forgejo_data)
if [ ! -d "$FORGEJO_DATA" ]; then
  echo "каталог данных Forgejo не найден: $FORGEJO_DATA" >&2
  exit 1
fi

# HELM v4.0 §14.16: приватное дерево health — отдельный КОРЕНЬ, а не
# подкаталог /opt/helm-knowledge, поэтому в бэкап оно не попадало бы само
# собой и его надо назвать явно.
#
# Исключения заякорены полными путями. Раньше стояло --exclude 'derived'
# без якоря, а restic сопоставляет такой шаблон с ЛЮБЫМ компонентом пути:
# каталог derived/ внутри приватного дерева (его предполагает та же §14.16
# для KnowledgeGraphify) молча не попал бы в бэкап. Найдено разбором при
# добавлении второго корня, а не отказом восстановления — что и есть
# правильный момент.
restic backup \
  "$WORKDIR/postgres-dumpall.sql" \
  "$WORKDIR/hermes-sqlite" \
  "$FORGEJO_DATA" \
  /opt/helm/n8n/exports \
  /opt/helm/config \
  /opt/helm/guardian \
  "$SECRETS_DIR" \
  /home/helm/.hermes/.env \
  /home/helm/.hermes/config.yaml \
  /home/helm/.hermes/auth.json \
  /home/helm/.hermes/channel_directory.json \
  /home/helm/.hermes/gateway_state.json \
  /home/helm/.hermes/SOUL.md \
  /home/helm/.hermes/cron \
  /home/helm/.hermes/hooks \
  /home/helm/.hermes/pairing \
  /home/helm/.hermes/platforms \
  /home/helm/.hermes/plugins \
  /home/helm/.hermes/profiles \
  /home/helm/.hermes/memories \
  /opt/helm-knowledge \
  /opt/helm-knowledge-private \
  --exclude '/opt/helm-knowledge/skills' \
  --exclude '/opt/helm-knowledge/derived' \
  --tag helm-daily \
  --pack-size 4

# Ретеншен по v4.0 §26.3: 7 daily / 4 weekly / 6 monthly. До 02.09.2026
# здесь стояло 3 monthly (дефолт, выбранный когда спека числа не задавала)
# — сверка с CURRENT spec это разошедшееся место и нашла. Числа обязаны
# совпадать с раскрытием сроков хранения в knowledge/offboarding.py, за
# этим следит test_retention_numbers_match_the_actual_backup_script.
restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune

# Guardian (§25) читает mtime этого файла как возраст последнего бэкапа.
touch /var/lib/helm-guardian/last-backup

echo "BACKUP DONE"
