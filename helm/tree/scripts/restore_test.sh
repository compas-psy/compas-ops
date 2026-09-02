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

# НАЙДЕНО 29.08.2026 на живом деплое: официальный образ postgres (и
# pgvector/pgvector, собранный поверх него) на ПЕРВОМ запуске стартует
# СЕРВЕР ДВАЖДЫ — временный (только чтобы прогнать init-скрипты), затем
# он останавливается и запускается финальный, уже принимающий рабочие
# подключения. pg_isready один раз успевает ответить "готов" ещё для
# временного сервера, который тут же гасится (сокет исчезает) — psql
# сразу после этого ловит гонку: "No such file or directory" на сокете,
# хотя проверка готовности секундой раньше прошла зелёной. Единственный
# надёжный сигнал именно финального запуска — вторая строка "database
# system is ready to accept connections" в логе контейнера (первая —
# от временного сервера); ждём именно её, а не однократный pg_isready.
ready=0
for _ in $(seq 1 60); do
  count=$(docker logs "$TEST_CONTAINER" 2>&1 | grep -c "database system is ready to accept connections" || true)
  if [ "$count" -ge 2 ]; then
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

# v3.8 §14.3/P8.6 acceptance: «backup/restore preserves owner + one
# secondary user». До мультитенантности проверки count(*) на tasks было
# достаточно; теперь потеря именно knowledge_users означала бы, что
# восстановленная база технически цела, а Вторые мозги в ней
# обезличены — все Knowledge-строки ссылаются на knowledge_user_id, и
# без реестра тенантов их некому принадлежать.
#
# psql идёт от postgres (суперпользователь) — RLS его не ограничивает,
# поэтому здесь видны строки ВСЕХ тенантов, что для проверки бэкапа и
# нужно.
OWNER_COUNT=$(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc \
  "select count(*) from knowledge_users where role = 'SYSTEM_OWNER'" 2>/dev/null || echo "нет")
if [ "$OWNER_COUNT" = "нет" ]; then
  # До накатки миграций v3.8 таблицы ещё нет — не провал бэкапа.
  echo "restore test: knowledge_users в снапшоте нет — миграции v3.8 ещё не накатаны"
else
  if [ "$OWNER_COUNT" -ne 1 ]; then
    echo "FAIL: в восстановленной базе ${OWNER_COUNT} строк SYSTEM_OWNER, ожидалась ровно 1" >&2
    exit 1
  fi
  SECONDARY_COUNT=$(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc \
    "select count(*) from knowledge_users where role = 'KNOWLEDGE_USER'")
  # Осиротевшие Knowledge-строки: ссылка на несуществующего тенанта —
  # признак частично восстановленной базы, худший из возможных исходов
  # (выглядит рабочей, а изоляция уже не та).
  ORPHANS=$(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc \
    "select count(*) from knowledge_sources s
      where not exists (select 1 from knowledge_users u where u.id = s.knowledge_user_id)")
  if [ "$ORPHANS" -ne 0 ]; then
    echo "FAIL: ${ORPHANS} knowledge_sources ссылаются на несуществующего тенанта" >&2
    exit 1
  fi
  SOURCE_COUNT=$(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc \
    "select count(*) from knowledge_sources")
  MEMORY_COUNT=$(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc \
    "select count(*) from knowledge_memories")
  echo "restore test: knowledge_users — владелец 1, вторичных ${SECONDARY_COUNT};" \
       "источников ${SOURCE_COUNT}, записей памяти ${MEMORY_COUNT}, сирот нет"

  # Markdown-зеркала Micro-Memory (§14.11) лежат по тенантам в
  # /opt/helm-knowledge/users/<uuid>/memory/ и попадают в снапшот вместе
  # с каталогом. Проверяем, что для каждой восстановленной строки памяти
  # зеркало тоже вернулось — иначе Obsidian/Graphify получили бы пустоту
  # при формально целой базе.
  MIRROR_COUNT=$(find "$RESTORE_DIR" -path '*/helm-knowledge/users/*/memory/*.md' | wc -l)
  if [ "$MEMORY_COUNT" -gt 0 ] && [ "$MIRROR_COUNT" -lt "$MEMORY_COUNT" ]; then
    echo "FAIL: записей памяти ${MEMORY_COUNT}, а Markdown-зеркал восстановлено ${MIRROR_COUNT}" >&2
    exit 1
  fi
  echo "restore test: Markdown-зеркал памяти восстановлено ${MIRROR_COUNT}"
fi

# Health-схема и приватное дерево (§14.16, R1 от 02.09.2026). Без этой
# проверки тест восстановления давал бы зелёный свет на снятие копии
# health-чанков из общей схемы, даже если в снапшоте приватных данных
# нет вовсе — а это ровно тот исход, ради которого тест и существует:
# база выглядит целой, а второго мозга в ней уже нет.
HEALTH_SCHEMA=$(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc \
  "select count(*) from information_schema.schemata where schema_name = 'health'")
if [ "$HEALTH_SCHEMA" = "1" ]; then
  HEALTH_CHUNKS=$(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc \
    "select count(*) from health.knowledge_chunks")
  HEALTH_SOURCES=$(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc \
    "select count(*) from health.knowledge_source_private")
  echo "restore test: health-схема восстановлена — чанков ${HEALTH_CHUNKS}," \
       "приватных источников ${HEALTH_SOURCES}"

  # Каждой строке сайдкара обязан соответствовать восстановленный файл.
  # Сверяем по имени файла, а не по полному пути: restic кладёт дерево
  # под свой префикс, и абсолютный путь из колонки здесь не совпадёт.
  if [ "$HEALTH_SOURCES" -gt 0 ]; then
    PRIVATE_MISSING=0
    while IFS= read -r stored; do
      [ -n "$stored" ] || continue
      name=$(basename "$stored")
      find "$RESTORE_DIR" -path '*/helm-knowledge-private/*' -name "$name" \
        -print -quit | grep -q . || PRIVATE_MISSING=$((PRIVATE_MISSING + 1))
    done < <(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc \
              "select stored_path from health.knowledge_source_private")
    if [ "$PRIVATE_MISSING" -gt 0 ]; then
      echo "FAIL: ${PRIVATE_MISSING} из ${HEALTH_SOURCES} приватных файлов health не восстановились" >&2
      exit 1
    fi
    echo "restore test: приватных файлов health восстановлено ${HEALTH_SOURCES} из ${HEALTH_SOURCES}"
  fi

  # Роли — часть изоляции, а не её оформление: без helm_health
  # восстановленная база вернёт данные, но не разграничение доступа.
  for role in helm_app helm_health; do
    HAS_ROLE=$(docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tAc \
      "select count(*) from pg_roles where rolname = '$role'")
    if [ "$HAS_ROLE" != "1" ]; then
      echo "FAIL: роль ${role} не восстановлена — изоляция health не воспроизводится" >&2
      exit 1
    fi
  done
  echo "restore test: роли helm_app и helm_health восстановлены"
else
  echo "restore test: health-схемы в снапшоте нет (снапшот старше R1)"
fi

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
