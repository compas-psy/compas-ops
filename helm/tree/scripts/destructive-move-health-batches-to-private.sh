#!/bin/bash
# HELM v4.0 RESCUE · R1, добор: перенести архивы health-пачек из общего
# дерева в приватное.
#
# НАЙДЕНО 02.09.2026 при приёмке §30.8.5 C. Миграция R1 перенесла
# оригиналы (`raw/health/*`) и конспекты (`sources/*`), но про
# `/opt/helm-knowledge/raw-batches/<id>/original.zip` в ней не было ни
# строки: архив пишется в `stage_batch()` ДО того, как станет известен
# домен, то есть всегда в общее дерево.
#
# Почему это не мелочь. Имена записей внутри архива —
# «Врачи/1209754_Консультация гастроэнтеролога.pdf», «Анализы и
# обследования/1239025_Биохимический анализ крови.pdf» — сами по себе
# рассказывают, к каким специалистам обращался владелец и какие анализы
# сдавал. Сайдкар заведён ровно затем, чтобы имя файла не лежало в общем
# контуре; архив с теми же именами лежал рядом, в открытом дереве.
#
# Почему через destructive-гейт, хотя это перенос, а не удаление. Файл
# меняет владельца и права, каталог в общем дереве убирается, а путь в
# базе переписывается. Ошибка здесь стоит владельцу исходников — это
# ровно тот класс операций, ради которого гейт и заведён.
set -uo pipefail

if [ "$(id -u)" -ne 0 ]; then exec sudo bash "$0" "$@"; fi

die() { echo "::error::операция отменена: $*"; exit 1; }
psql() { docker exec -i helm-postgres-1 psql -U helm -d helm -v ON_ERROR_STOP=1 "$@"; }

WORK=$(mktemp -d /var/lib/helm-guardian/move-batches-XXXXXX)
trap 'rm -rf "$WORK"' EXIT

q() {
  if ! psql -tAc "$1" > "$WORK/q.out" 2> "$WORK/q.err" < /dev/null; then
    echo "::error::запрос не выполнился:" >&2
    sed 's/^/    /' "$WORK/q.err" >&2
    exit 1
  fi
  cat "$WORK/q.out"
}

PRIVATE=/opt/helm-knowledge-private
COMMON=/opt/helm-knowledge

echo "############ 0. СТРАХОВКА ############"
fresh() {
  [ -f "$1" ] || die "нет отметки $1"
  age=$(( ( $(date +%s) - $(stat -c %Y "$1") ) / 3600 ))
  echo "  $1: $age ч назад (предел $2 ч)"
  [ "$age" -le "$2" ] || die "$1 старше $2 ч"
}
fresh /var/lib/helm-guardian/last-backup 24
fresh /var/lib/helm-guardian/last-restore-test 168

echo
echo "############ 1. СВОЯ ТОЧКА ВОЗВРАТА ############"
/opt/helm/scripts/local-rescue-checkpoint.sh create >/dev/null \
  || die "локальная точка возврата не снялась"
echo "  снята"

echo
echo "############ 2. ЧТО ПЕРЕНОСИМ ############"
q "
  select b.id || E'\t' || b.knowledge_user_id || E'\t' || b.archive_raw_path
  from knowledge_ingest_batches b
  where b.domain = 'health'
    and b.archive_raw_path like '$COMMON/%'
  order by b.created_at" > "$WORK/batches.tsv" || die "не удалось прочитать список пачек"

count=$(wc -l < "$WORK/batches.tsv")
echo "  health-пачек с архивом в общем дереве: $count"
[ "$count" -gt 0 ] || { echo "  переносить нечего"; exit 0; }
sed 's/^/    /' "$WORK/batches.tsv"

echo
echo "############ 3. ПЕРЕНОС ############"
moved=0
while IFS=$'\t' read -r bid uid src; do
  [ -n "$bid" ] || continue
  [ -f "$src" ] || die "архив пачки $bid не найден: $src"

  dest_dir="$PRIVATE/health/users/$uid/raw-batches/$bid"
  dest="$dest_dir/$(basename "$src")"
  mkdir -p "$dest_dir"

  # Сначала копия с проверкой хэша, потом запись в базу, потом удаление
  # исходника. Порядок важен: на любом обрыве данные остаются целы хотя
  # бы в одном месте, и ни одна строка базы не указывает в пустоту.
  before=$(sha256sum "$src" | cut -d' ' -f1)
  cp -p "$src" "$dest" || die "не удалось скопировать $src"
  after=$(sha256sum "$dest" | cut -d' ' -f1)
  [ "$before" = "$after" ] || die "копия не совпала по sha256: $dest"

  q "update knowledge_ingest_batches
        set archive_raw_path = '$dest'
      where id = '$bid'" >/dev/null

  rm -f "$src"
  rmdir "$(dirname "$src")" 2>/dev/null || true
  moved=$((moved + 1))
  echo "  $bid → $dest"
done < "$WORK/batches.tsv"

chown -R root:helm-health "$PRIVATE"
chmod -R 2770 "$PRIVATE"
echo "  перенесено: $moved"

echo
echo "############ 4. ПОСЛЕ ############"
left_db=$(q "
  select count(*) from knowledge_ingest_batches
  where domain = 'health' and archive_raw_path like '$COMMON/%'")
left_fs=$(find "$COMMON" -name '*.zip' -type f | wc -l)
in_private=$(find "$PRIVATE" -name '*.zip' -type f | wc -l)
common_files=$(find "$COMMON" -type f | wc -l)

echo "  строк в базе с путём в общем дереве: $left_db (ожидается 0)"
echo "  архивов в общем дереве:              $left_fs (ожидается 0)"
echo "  архивов в приватном дереве:          $in_private (ожидается $moved)"
echo "  всего файлов в общем дереве:         $common_files"

[ "$left_db" = "0" ]      || die "$left_db строк всё ещё указывают в общее дерево"
[ "$left_fs" = "0" ]      || die "$left_fs архивов осталось в общем дереве"
[ "$in_private" = "$moved" ] || die "в приватном дереве $in_private архивов вместо $moved"

echo
echo "############ ГОТОВО ############"
