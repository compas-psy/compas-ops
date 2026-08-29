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

touch /var/lib/helm-guardian/last-restore-test
echo "RESTORE TEST PASSED"
