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

log() { printf '%s %s\n' "$(date -Is)" "$*"; }
run() {
  if (( APPLY )); then "$@"; else log "DRY-RUN: $*"; fi
}

if ! command -v docker >/dev/null 2>&1; then
  log "docker не установлен — очистка Docker пропущена"
  exit 0
fi

# 1. Висячие образы. Безопасно: на них никто не ссылается.
log "== dangling images =="
run docker image prune -f

# 2. Кэш сборки сверх целевого объёма. keep-storage сохраняет свежий слой.
log "== build cache свыше ${BUILD_CACHE_TARGET} =="
run docker builder prune -f --keep-storage "${BUILD_CACHE_TARGET}"

# 3. Остановленные одноразовые контейнеры. Только помеченные как
#    disposable: чужой остановленный контейнер может ждать отладки.
log "== остановленные одноразовые контейнеры =="
run docker container prune -f --filter "label=helm.disposable=true"

# 4. Неиспользуемые образы старше retention. Именно `image prune -a` с
#    фильтром времени, НЕ `system prune -a --volumes`.
log "== неиспользуемые образы старше ${IMAGE_RETENTION} =="
run docker image prune -af --filter "until=${IMAGE_RETENTION}"

# 5. Просроченные workspace и temp Hermes.
log "== просроченные workspaces/temp =="
for dir in /opt/helm-state/workspaces /opt/helm-state/temp; do
  [[ -d "$dir" ]] || continue
  if (( APPLY )); then
    find "$dir" -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -rf {} +
  else
    find "$dir" -mindepth 1 -maxdepth 1 -type d -mtime +7 -printf 'DRY-RUN: rm -rf %p\n'
  fi
done

# Named volumes не трогаются никогда и ни при каком заполнении диска.
log "named volumes не затрагиваются (§25.6)"
log "готово$( ((APPLY)) || echo ' (dry-run; повторите с --apply)')"
