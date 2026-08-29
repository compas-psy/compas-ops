#!/bin/bash
# Restore test (§25.2, §26.4, A-DoD п.10): восстановить последний снапшот
# в ИЗОЛИРОВАННОЕ тестовое окружение — временный Postgres-контейнер, не
# продовый. Не трогает реальные /opt/helm, /home/helm/.hermes, БД.
set -euo pipefail

SECRETS_DIR=/etc/helm/secrets
export RESTIC_REPOSITORY="rclone:yandex:helm-backup"
export RESTIC_PASSWORD_FILE="$SECRETS_DIR/restic_password"

RESTORE_DIR=$(mktemp -d /var/lib/helm-guardian/restore-test-XXXXXX)
TEST_CONTAINER="helm-restore-test-$$"
cleanup() {
  docker rm -f "$TEST_CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$RESTORE_DIR"
}
trap cleanup EXIT

echo "== restic restore последнего снапшота =="
restic restore latest --target "$RESTORE_DIR"

DUMP=$(find "$RESTORE_DIR" -name postgres-dumpall.sql | head -1)
if [ -z "$DUMP" ] || [ ! -s "$DUMP" ]; then
  echo "FAIL: postgres-dumpall.sql не найден или пуст после restore" >&2
  exit 1
fi

echo "== поднимаю временный Postgres для проверки дампа =="
docker run --rm -d --name "$TEST_CONTAINER" \
  -e POSTGRES_PASSWORD=restoretest \
  pgvector/pgvector:pg16 >/dev/null

ready=0
for _ in $(seq 1 60); do
  if docker exec "$TEST_CONTAINER" pg_isready -U postgres >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "FAIL: тестовый Postgres не поднялся за 60с. Логи контейнера:" >&2
  docker logs "$TEST_CONTAINER" 2>&1 | tail -50 >&2
  exit 1
fi

echo "== загружаю дамп в тестовый контейнер =="
docker exec -i "$TEST_CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 < "$DUMP" >/dev/null

TASK_COUNT=$(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc "select count(*) from tasks")
echo "restore test: таблица tasks восстановлена, строк: ${TASK_COUNT}"

PROFILE_COUNT=$(find "$RESTORE_DIR" -path '*/profiles/*/config.yaml' | wc -l)
echo "restore test: профилей Hermes восстановлено: ${PROFILE_COUNT}"

if [ "$PROFILE_COUNT" -lt 4 ]; then
  echo "FAIL: ожидалось минимум 4 файла profiles/*/config.yaml, найдено ${PROFILE_COUNT}" >&2
  exit 1
fi

echo "== проверяю восстановленные репозитории Forgejo (§18.7) =="
# Распоряжение владельца от 29.08.2026: restore-test обязан проверять
# минимум один репозиторий Forgejo — refs, tags, HEAD.
#
# git запускается в одноразовом контейнере, а не на хосте: на хосте его
# может не быть вовсе (деплой здесь scp-based, не git-based), и проверка
# молча выродилась бы в «команда не найдена».
BARE_REPO=$(find "$RESTORE_DIR" -type d -name '*.git' -path '*/repositories/*' | head -1)
if [ -z "$BARE_REPO" ]; then
  # До миграции (§18.3) репозиториев ещё нет — это не провал бэкапа.
  # Проверка включится сама, как только они появятся.
  echo "restore test: репозиториев Forgejo в снапшоте нет — миграция ещё не выполнена"
else
  REPO_NAME=$(basename "$BARE_REPO")
  GIT="docker run --rm -v $BARE_REPO:/repo:ro -w /repo \
       --entrypoint git codeberg.org/forgejo/forgejo:15.0.3"

  if ! $GIT --git-dir=/repo rev-parse HEAD >/dev/null 2>&1; then
    echo "FAIL: HEAD не разрешается в репозитории ${REPO_NAME}" >&2
    exit 1
  fi
  REF_COUNT=$($GIT --git-dir=/repo show-ref | wc -l)
  if [ "$REF_COUNT" -lt 1 ]; then
    echo "FAIL: в репозитории ${REPO_NAME} не восстановлено ни одного ref" >&2
    exit 1
  fi
  # fsck без --full: проверяет связность объектов, на которые указывают
  # refs, — ровно тот случай порчи, который возможен при копировании
  # живого репозитория (ref обновлён, объект не скопирован).
  if ! $GIT --git-dir=/repo fsck --connectivity-only --no-progress >/dev/null 2>&1; then
    echo "FAIL: git fsck нашёл повреждения в ${REPO_NAME}" >&2
    exit 1
  fi
  TAG_COUNT=$($GIT --git-dir=/repo tag -l | wc -l)
  echo "restore test: ${REPO_NAME} — refs: ${REF_COUNT}, tags: ${TAG_COUNT}, HEAD цел, fsck чист"
fi

touch /var/lib/helm-guardian/last-restore-test
echo "RESTORE TEST PASSED"
