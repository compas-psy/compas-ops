#!/usr/bin/env bash
# Автоочистка Guardian (ТЗ §25.6).
#
# Список разрешённого — закрытый. Всё, чего в нём нет, не удаляется
# автоматически, даже если место кончается: §25.6 прямо запрещает
# `docker system prune -a --volumes`, удаление named volumes, БД и активных
# релизов. Диск, забитый на 90%, — это инцидент; удалённый named volume —
# это потеря данных, и второе хуже.
#
# Запуск без --apply — только показать, что было бы сделано.

set -euo pipefail

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

BUILD_CACHE_TARGET="4GB"   # §25.6: цель 3–5 GB
IMAGE_RETENTION="168h"     # неделя

FAILED=0

log() { printf '%s %s\n' "$(date -Is)" "$*"; }
run() {
  if (( APPLY )); then
    # Провал одного шага не обрывает остальные, но остаётся виден в журнале
    # и делает выход ненулевым. ИЗМЕРЕНО (прогон 452): `--keep-storage`
    # исчез в docker 29.x, а при `set -euo pipefail` без этой обёртки
    # первая же такая ошибка молча убивала уборку целиком — шаги 3-5 не
    # выполнялись, и никто об этом не узнавал.
    "$@" || { log "ОШИБКА: $*"; FAILED=1; }
  else
    log "DRY-RUN: $*"
  fi
}

if ! command -v docker >/dev/null 2>&1; then
  log "docker не установлен — очистка Docker пропущена"
  exit 0
fi

# 1. Висячие образы. Безопасно: на них никто не ссылается.
log "== dangling images =="
run docker image prune -f

# 2. Кэш сборки сверх целевого объёма. max-used-space вытесняет самые
#    давние слои, свежие остаются — следующая сборка не идёт с нуля.
log "== build cache свыше ${BUILD_CACHE_TARGET} =="
run docker builder prune -f --max-used-space "${BUILD_CACHE_TARGET}"

# 3. Остановленные одноразовые контейнеры. Только помеченные как
#    disposable: чужой остановленный контейнер может ждать отладки.
log "== остановленные одноразовые контейнеры =="
run docker container prune -f --filter "label=helm.disposable=true"

# 4. Неиспользуемые образы старше retention. Именно `image prune -a` с
#    фильтром времени, НЕ `system prune -a --volumes`.
#
#    ОТКАТУ ЭТО НЕ МЕШАЕТ — проверено по самому механизму отката
#    (08.09.2026, распоряжение п.5). Откат схемы в `deploy.yml` это
#    `alembic downgrade` внутри УЖЕ ЗАПУЩЕННОГО контейнера; откат кода —
#    обычный выкат прежней ревизии, который пересобирает образ из
#    исходников. Ни то, ни другое не берёт старый образ с диска. Точки
#    возврата (`local-rescue-checkpoint.sh`) хранят дамп Postgres и
#    конфигурацию, образов в них нет вовсе. Плюс фильтр `-a` удаляет
#    только то, на чём не стоит ни один контейнер.
log "== неиспользуемые образы старше ${IMAGE_RETENTION} =="
run docker image prune -af --filter "until=${IMAGE_RETENTION}"

# 5. Просроченные workspace и temp Hermes.
#
# ЧЕРЕЗ ТУ ЖЕ ОБЁРТКУ, ЧТО И ОСТАЛЬНЫЕ ШАГИ. Раньше этот шаг звал `find`
# напрямую, в обход `run()`: при `set -e` его сбой обрывал скрипт молча,
# а заявленный контракт («провал шага виден, не обрывает остальные,
# делает выход ненулевым») на него не распространялся. Один шаг из пяти
# жил по своим правилам — ровно так и возвращаются молчаливые отказы.
log "== просроченные workspaces/temp =="
for dir in /opt/helm-state/workspaces /opt/helm-state/temp; do
  [[ -d "$dir" ]] || continue
  run find "$dir" -mindepth 1 -maxdepth 1 -type d -mtime +7 -delete
done

# Named volumes не трогаются никогда и ни при каком заполнении диска.
log "named volumes не затрагиваются (§25.6)"
log "готово$( ((APPLY)) || echo ' (dry-run; повторите с --apply)')"

# ОТМЕТКА ДЛЯ МОНИТОРИНГА. Guardian смотрит на эти два файла
# (`check_cleanup`): по свежести первого видно, что уборка идёт, по
# существованию второго — что последняя попытка не удалась. Без отметки
# о неисправности уборки узнают по заполненному диску, как 07.09.2026.
STATE_DIR=/var/lib/helm-guardian
if (( APPLY )) && [[ -d "$STATE_DIR" ]]; then
  if (( FAILED )); then
    touch "$STATE_DIR/last-cleanup-failed"
  else
    rm -f "$STATE_DIR/last-cleanup-failed"
    touch "$STATE_DIR/last-cleanup"
  fi
fi

if (( FAILED )); then
  log "уборка выполнена НЕ ПОЛНОСТЬЮ: см. строки ОШИБКА выше"
  exit 1
fi
