#!/bin/bash
# Restore test (§25.2, §26.4, A-DoD п.10): восстановить последний снапшот
# в ИЗОЛИРОВАННОЕ тестовое окружение — временный Postgres-контейнер, не
# продовый. Не трогает реальные /opt/helm, /home/helm/.hermes, БД.
set -euo pipefail

SECRETS_DIR=/etc/helm/secrets
export RESTIC_REPOSITORY="rclone:yandex:helm-backup"
export RESTIC_PASSWORD_FILE="$SECRETS_DIR/restic_password"

RESTORE_DIR=$(mktemp -d /var/lib/helm-guardian/restore-test-XXXXXX)
#: Рабочий каталог проверок: вывод SQL пишется в файлы, а не читается
#: через подстановку процессов, где код возврата теряется (см. разбор
#: перед health-проверками ниже).
HEALTH_TMP=$(mktemp -d /var/lib/helm-guardian/restore-check-XXXXXX)
TEST_CONTAINER="helm-restore-test-$$"
cleanup() {
  docker rm -f "$TEST_CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$RESTORE_DIR" "$HEALTH_TMP"
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

# ── Health: схема, файлы, роли (§14.16, §30.8.5 C) ───────────────────
#
# НАЙДЕНО ВЛАДЕЛЬЦЕМ 02.09.2026 в прогоне #152. Первая версия этой
# проверки спрашивала `stored_path` и `sha256` у
# `health.knowledge_source_private`. Таких колонок там нет и не было:
# сайдкар хранит только `source_id`, `knowledge_user_id`,
# `original_filename`, `parse_error`, `created_at`. Путь и хэш файла —
# в публичном конверте `knowledge_sources` (`raw_path`, `source_path`,
# `sha256`), и приватно там как раз ничего нет: чувствительное имя
# файла живёт в сайдкаре, а хэш-производный путь — нет.
#
# Хуже самой ошибки было то, как она себя повела. Запрос падал с
# `ERROR: column "stored_path" does not exist`, но стоял внутри
# process substitution `< <(...)`. Код возврата подстановки основной
# оболочке не виден, `set -e` на неё не распространяется: цикл получил
# ноль строк, счётчик пропаж остался нулём, и тест напечатал
# «восстановлено 90 из 90» и `RESTORE TEST PASSED`. Зелёный тест
# восстановления, не проверивший ни одного файла, — худший из возможных
# отказов страховки: он не молчит, он врёт.
#
# Поэтому здесь: никаких подстановок процессов. Каждый запрос пишется во
# временный файл, код возврата проверяется сразу, и любой отказ SQL —
# фатален.

q() {
  # Единственный способ обратиться к восстановленной базе. Падение psql
  # завершает тест, а не теряется в подоболочке.
  # `< /dev/null` — чтобы запрос никогда не съел stdin вызывающего кода.
  # Здесь цикл читает из файла и q() внутри него не вызывается, но именно
  # так дефект и появляется: сначала не нужно, потом кто-то добавит вызов.
  if ! docker exec "$TEST_CONTAINER" psql -U postgres -d helm -tA \
       -v ON_ERROR_STOP=1 -c "$1" > "$HEALTH_TMP/q.out" 2> "$HEALTH_TMP/q.err" \
       < /dev/null; then
    echo "FAIL: запрос к восстановленной базе не выполнился" >&2
    sed 's/^/    /' "$HEALTH_TMP/q.err" >&2
    exit 1
  fi
  cat "$HEALTH_TMP/q.out"
}

HEALTH_SCHEMA=$(q "select count(*) from information_schema.schemata where schema_name = 'health'")
if [ "$HEALTH_SCHEMA" = "1" ]; then
  HEALTH_CHUNKS=$(q "select count(*) from health.knowledge_chunks")
  HEALTH_SIDECARS=$(q "select count(*) from health.knowledge_source_private")
  HEALTH_EMBEDDINGS=$(q "select count(*) from health.knowledge_chunks where embedding is not null")
  HEALTH_ENVELOPES=$(q "select count(*) from knowledge_sources where domain = 'health'")
  echo "restore test: health — чанков ${HEALTH_CHUNKS} (с вектором ${HEALTH_EMBEDDINGS})," \
       "сайдкаров ${HEALTH_SIDECARS}, конвертов ${HEALTH_ENVELOPES}"

  # Каждому health-конверту обязан соответствовать сайдкар: конверт без
  # сайдкара означает, что приватная часть источника не восстановилась.
  ORPHAN_ENVELOPES=$(q "
    select count(*) from knowledge_sources s
    where s.domain = 'health'
      and not exists (select 1 from health.knowledge_source_private p
                      where p.source_id = s.id)")
  if [ "$ORPHAN_ENVELOPES" != "0" ]; then
    echo "FAIL: ${ORPHAN_ENVELOPES} health-конвертов без сайдкара в восстановленной базе" >&2
    exit 1
  fi

  # ── файлы: 90 оригиналов RAW и 90 конспектов L1, всего 180 ──────────
  # Путь в базе абсолютный, restic кладёт дерево под свой префикс —
  # значит файл ищется по "$RESTORE_DIR" + абсолютный путь, а не по
  # имени где-то в дереве. Поиск по имени нашёл бы файл, лежащий не там,
  # где его будет искать выдача оригинала, и это снова был бы зелёный
  # тест при сломанной системе.
  q "select id || E'\t' || sha256 || E'\t' || raw_path || E'\t' || coalesce(source_path, '')
     from knowledge_sources where domain = 'health' order by id" > "$HEALTH_TMP/files.tsv"

  RAW_OK=0; RAW_MISSING=0; RAW_MISMATCH=0
  L1_OK=0; L1_MISSING=0; L1_ABSENT=0
  OUTSIDE=0
  PRIVATE_PREFIX=/opt/helm-knowledge-private

  while IFS=$'\t' read -r sid sha raw src; do
    [ -n "$sid" ] || continue

    case "$raw" in "$PRIVATE_PREFIX"/*) ;; *) OUTSIDE=$((OUTSIDE + 1)); echo "  ВНЕ ПРИВАТНОГО ДЕРЕВА: $raw" ;; esac
    if [ -f "$RESTORE_DIR$raw" ]; then
      if [ "$(sha256sum "$RESTORE_DIR$raw" | cut -d' ' -f1)" = "$sha" ]; then
        RAW_OK=$((RAW_OK + 1))
      else
        RAW_MISMATCH=$((RAW_MISMATCH + 1)); echo "  ХЭШ РАЗОШЁЛСЯ: $raw"
      fi
    else
      RAW_MISSING=$((RAW_MISSING + 1)); echo "  НЕТ ОРИГИНАЛА: $raw"
    fi

    # L1-конспект: sha в базе для него не хранится, поэтому проверяем
    # существование и то, что путь внутри приватного дерева. Меньше, чем
    # для оригинала, — и это честно названо, а не выдано за полную сверку.
    if [ -z "$src" ]; then
      L1_ABSENT=$((L1_ABSENT + 1)); echo "  НЕТ source_path В БАЗЕ: $sid"
    else
      case "$src" in "$PRIVATE_PREFIX"/*) ;; *) OUTSIDE=$((OUTSIDE + 1)); echo "  ВНЕ ПРИВАТНОГО ДЕРЕВА: $src" ;; esac
      if [ -f "$RESTORE_DIR$src" ]; then
        L1_OK=$((L1_OK + 1))
      else
        L1_MISSING=$((L1_MISSING + 1)); echo "  НЕТ КОНСПЕКТА: $src"
      fi
    fi
  done < "$HEALTH_TMP/files.tsv"

  echo "restore test: оригиналов ${RAW_OK}/${HEALTH_ENVELOPES} (нет ${RAW_MISSING}, хэш разошёлся ${RAW_MISMATCH})," \
       "конспектов ${L1_OK}/${HEALTH_ENVELOPES} (нет ${L1_MISSING}, без пути ${L1_ABSENT})," \
       "путей вне приватного дерева ${OUTSIDE}"

  # ── жёсткие утверждения ────────────────────────────────────────────
  # Числа не «ожидаются примерно»: каждое расхождение это отказ. Пустой
  # корпус тоже отказ — иначе тест на пустой базе всегда зелёный.
  fail=0
  assert_eq() {
    if [ "$2" != "$3" ]; then
      echo "FAIL: $1 — $2, ожидалось $3" >&2
      fail=1
    fi
  }
  [ "$HEALTH_ENVELOPES" -gt 0 ] || { echo "FAIL: health-конвертов ноль — проверять нечего" >&2; fail=1; }
  assert_eq "сайдкаров"                 "$HEALTH_SIDECARS" "$HEALTH_ENVELOPES"
  assert_eq "восстановлено оригиналов"  "$RAW_OK"          "$HEALTH_ENVELOPES"
  assert_eq "восстановлено конспектов"  "$L1_OK"           "$HEALTH_ENVELOPES"
  assert_eq "оригиналов не найдено"     "$RAW_MISSING"     "0"
  assert_eq "хэш разошёлся"             "$RAW_MISMATCH"    "0"
  assert_eq "конспектов не найдено"     "$L1_MISSING"      "0"
  assert_eq "источников без source_path" "$L1_ABSENT"      "0"
  assert_eq "путей вне приватного дерева" "$OUTSIDE"       "0"
  [ "$HEALTH_CHUNKS" -gt 0 ] || { echo "FAIL: health-чанков ноль" >&2; fail=1; }
  [ "$HEALTH_EMBEDDINGS" -gt 0 ] || { echo "FAIL: ни одного вектора в health" >&2; fail=1; }

  # ── состояние ПОСЛЕ R1: копии health в общей схеме быть не должно ──
  # Распоряжение владельца 02.09.2026, пункт «post-migration recovery
  # checkpoint». Восстановиться в состояние ДО R1 — значит вернуть
  # копию health-текста в общую схему, то есть отменить приватность,
  # ничего об этом не сказав. Точка восстановления, возвращающая систему
  # в починенное состояние, — часть починки, а не следствие.
  PUBLIC_HEALTH=$(q "
    select count(*) from knowledge_chunks c
    join knowledge_sources s on s.id = c.source_id
    where s.domain = 'health'")
  echo "restore test: health-чанков в общей схеме после восстановления: ${PUBLIC_HEALTH}"
  if [ "$PUBLIC_HEALTH" != "0" ]; then
    echo "FAIL: в восстановленной базе ${PUBLIC_HEALTH} health-чанков в общей схеме" >&2
    echo "      снапшот снят до R1 — восстановление из него отменяет приватность" >&2
    fail=1
  fi

  # helm_app не должен читать health и в восстановленной базе: гранты
  # едут в дампе вместе с ролями, и потерять их — значит восстановить
  # данные без разграничения доступа к ним.
  for t in knowledge_chunks knowledge_notes knowledge_relations knowledge_source_private; do
    if [ "$(q "select has_table_privilege('helm_app', 'health.${t}', 'select')")" != "f" ]; then
      echo "FAIL: helm_app читает health.${t} в восстановленной базе" >&2
      fail=1
    fi
  done

  # Роли — часть изоляции, а не её оформление: без helm_health
  # восстановленная база вернёт данные, но не разграничение доступа.
  for role in helm_app helm_health; do
    if [ "$(q "select count(*) from pg_roles where rolname = '$role'")" != "1" ]; then
      echo "FAIL: роль ${role} не восстановлена — изоляция health не воспроизводится" >&2
      fail=1
    fi
  done

  # Архивы пачек: тот же контур, что и файлы. Найдено 02.09.2026 при
  # приёмке — ZIP с медицинскими документами лежали в общем дереве, и
  # снапшот, где они там же, возвращает утечку вместе с данными.
  COMMON_ZIPS=$(find "$RESTORE_DIR" -path '*/helm-knowledge/raw-batches/*' -name '*.zip' | wc -l)
  if [ "$COMMON_ZIPS" != "0" ]; then
    echo "FAIL: в восстановленном общем дереве ${COMMON_ZIPS} архивов пачек" >&2
    fail=1
  fi

  [ "$fail" = "0" ] || exit 1
  echo "restore test: health проверен целиком — ${HEALTH_ENVELOPES} источников," \
       "$((RAW_OK + L1_OK)) файлов, роли на месте"
else
  # Снапшот старше R1. Это НЕ повод для зелёного теста: с 02.09.2026
  # приватное дерево существует, и снапшот без него не годится как
  # страховка для необратимого шага.
  echo "FAIL: в снапшоте нет health-схемы — он снят до миграции R1 и приватных данных не содержит" >&2
  exit 1
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
