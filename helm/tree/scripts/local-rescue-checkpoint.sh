#!/bin/bash
# Локальная точка возврата на самом VPS — обязательное условие любого
# безопасного выката (deployment policy от 02.09.2026, решение владельца).
#
# Зачем отдельно от backup.sh. Тот кладёт зашифрованную копию в удалённое
# хранилище через restic/rclone и в этом его ценность: он переживает
# потерю машины. Но он же зависит от стороннего сервиса, который 02.09
# трижды подряд отдал 500, съев по двадцать минут на попытку. Держать
# готовый и протестированный код заложником чужого сервиса — неверно;
# выкатываться вообще без возможности откатиться — тоже. Отсюда два
# разных механизма с разными задачами:
#
#   локальная точка возврата  защищает от ОШИБКИ ВЫКАТА, снимается за
#                             десятки секунд, обязательна перед deploy
#   offsite restic            защищает от ПОТЕРИ МАШИНЫ, идёт отдельно и
#                             обязателен перед необратимыми операциями
#
# Локальная копия лежит на том же диске, что и данные. Это осознанный
# предел: от отказа диска она не спасает и заменой offsite не является.
#
# Секреты сюда НЕ копируются (CLAUDE.md §5.4) — только имена и
# контрольные суммы, как в checkpoint.sh. Значения живут в GitHub Secrets
# и в зашифрованном offsite-бэкапе.
#
#   local-rescue-checkpoint.sh            снять точку возврата
#   local-rescue-checkpoint.sh verify     проверить последнюю на читаемость
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then exec sudo bash "$0" "$@"; fi

ROOT=/opt/helm-rescue-checkpoints
KEEP=3
#: Ниже этого порога не начинаем: оборванная на полпути точка возврата
#: хуже честного отказа — она выглядит существующей.
MIN_FREE_MB=4096

die() { echo "::error::локальная точка возврата: $*"; exit 1; }

# ── verify ────────────────────────────────────────────────────────────
if [ "${1:-create}" = "verify" ]; then
  latest=$(ls -1d "$ROOT"/*/ 2>/dev/null | sort | tail -1) || true
  [ -n "${latest:-}" ] || die "ни одной точки возврата в $ROOT"
  cd "$latest"
  [ -f SHA256SUMS ] || die "в $latest нет SHA256SUMS"
  sha256sum -c SHA256SUMS >/dev/null || die "контрольные суммы не сошлись в $latest"
  for archive in *.tar.gz *.sql.gz; do
    [ -e "$archive" ] || continue
    gzip -t "$archive" || die "архив повреждён: $latest$archive"
  done
  echo "точка возврата читается: $latest"
  sed -n '1,12p' MANIFEST.txt
  exit 0
fi

# ── create ────────────────────────────────────────────────────────────
free_mb=$(df -Pm /opt | awk 'NR==2 {print $4}')
[ "$free_mb" -ge "$MIN_FREE_MB" ] || die "свободно ${free_mb} МБ, нужно минимум ${MIN_FREE_MB} МБ"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
dir="$ROOT/$stamp"
mkdir -p "$dir"
chmod 700 "$ROOT" "$dir"

echo "снимаю локальную точку возврата → $dir"

# 1. База целиком. pg_dumpall, а не pg_dump: роли и права — часть того,
#    что делает health-изоляцию изоляцией, и без них восстановление
#    вернуло бы данные, но не разграничение доступа к ним.
docker exec helm-postgres-1 pg_dumpall -U helm | gzip -1 > "$dir/postgres-dumpall.sql.gz"
[ -s "$dir/postgres-dumpall.sql.gz" ] || die "дамп базы пуст"

# 2. Оба дерева знаний. Приватное — отдельным архивом, не вместе с общим:
#    восстанавливать их может понадобиться по отдельности, и смешивать
#    health с остальным в одном файле противоречит смыслу разделения.
tar -czf "$dir/helm-knowledge.tar.gz" -C /opt helm-knowledge
if [ -d /opt/helm-knowledge-private ]; then
  tar -czf "$dir/helm-knowledge-private.tar.gz" -C /opt helm-knowledge-private
fi

# 3. Конфигурация, от которой зависит запуск. Без docker-compose.yml и
#    Caddyfile восстановленные данные некому обслуживать.
[ -d /opt/helm/compose ] || die "нет /opt/helm/compose — это не боевой сервер?"
parts=()
for d in compose config scripts guardian; do
  [ -e "/opt/helm/$d" ] && parts+=("$d")
done
tar -czf "$dir/config.tar.gz" -C /opt/helm "${parts[@]}" \
  || die "не удалось заархивировать конфигурацию"

# 4. Секреты — только имена, права и контрольные суммы. Значений здесь
#    нет и быть не должно.
{
  echo "# Только имена, права и контрольные суммы. Значений здесь нет."
  for d in /etc/helm/secrets /etc/helm/backup /etc/helm/ssh; do
    [ -d "$d" ] || continue
    find "$d" -type f -printf '%p\t%m\t%u:%g\t' -exec sha256sum {} \; \
      | awk '{print $1"\t"$2"\t"$3"\t"$4}'
  done
} > "$dir/secrets-fingerprint.txt"

# 5. Манифест: то, по чему потом можно понять, ЧТО именно в этой точке
#    возврата, не разворачивая её.
{
  echo "снято:            $stamp UTC"
  echo "выкаченная ревизия: $(cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo 'отметки нет')"
  echo "ревизия схемы:    $(cd /opt/helm/compose && docker compose exec -T helm-core python3 -m alembic current 2>/dev/null | tail -1)"
  echo
  echo "-- строки в ключевых таблицах --"
  docker exec helm-postgres-1 psql -U helm -d helm -tAc "
    select 'knowledge_sources: '   || count(*) from knowledge_sources
    union all select 'public.knowledge_chunks: ' || count(*) from knowledge_chunks
    union all select 'health.knowledge_chunks: ' || count(*) from health.knowledge_chunks
    union all select 'knowledge_memories: '      || count(*) from knowledge_memories
    union all select 'knowledge_notes: '         || count(*) from knowledge_notes"
  echo
  echo "-- файлы --"
  echo "общий Vault:      $(find /opt/helm-knowledge -type f | wc -l) файлов, $(du -sh /opt/helm-knowledge | cut -f1)"
  if [ -d /opt/helm-knowledge-private ]; then
    echo "приватное дерево: $(find /opt/helm-knowledge-private -type f | wc -l) файлов, $(du -sh /opt/helm-knowledge-private | cut -f1)"
  fi
  echo
  echo "-- состав точки возврата --"
  ls -lh "$dir" | tail -n +2 | awk '{print $9"\t"$5}'
} > "$dir/MANIFEST.txt"

# 6. Контрольные суммы — то, чем verify отличает целую точку возврата от
#    оборвавшейся на полпути.
( cd "$dir" && sha256sum ./*.gz ./*.txt > SHA256SUMS )

# 7. Немедленная самопроверка. Точка возврата, целость которой не
#    проверили сразу, — обещание, а не страховка.
( cd "$dir" && sha256sum -c SHA256SUMS >/dev/null ) || die "самопроверка не сошлась"
for archive in "$dir"/*.gz; do gzip -t "$archive" || die "архив повреждён: $archive"; done

# 8. Ретеншен: держим последние $KEEP, остальное удаляем.
ls -1d "$ROOT"/*/ 2>/dev/null | sort | head -n -"$KEEP" | while read -r old; do
  echo "удаляю старую точку возврата: $old"
  rm -rf "$old"
done

# Отметка для Guardian и для гейта необратимых операций.
mkdir -p /var/lib/helm-guardian
touch /var/lib/helm-guardian/last-local-checkpoint

echo
echo "готово: $dir"
cat "$dir/MANIFEST.txt"
