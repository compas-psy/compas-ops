#!/bin/bash
# HELM v4.0 RESCUE · R1, завершение: убрать локальные точки возврата,
# снятые ДО снятия health-копии.
#
# Распоряжение владельца 02.09.2026, пункт 6. Локальная точка возврата,
# сделанная непосредственно перед необратимым шагом, содержит дамп базы
# с 953 health-чанками в ОБЩЕЙ схеме — то есть ровно то состояние, из
# которого R1 выводил. Лежит она незашифрованной на том же диске.
#
# Пока новой точки восстановления не было, эта — единственный быстрый
# откат, и трогать её нельзя. После того как offsite-снапшот снят уже
# ПОСЛЕ снятия копии и проверен восстановлением, она перестаёт быть
# страховкой и остаётся только незашифрованным слепком того, что мы
# убирали. Такой слепок не должен лежать бессрочно.
#
# Что считается pre-R1: в манифесте точки возврата
# `public.knowledge_chunks` больше нуля. Не дата и не имя — состояние,
# записанное в самой точке. Дата обманет при любом сдвиге часов, имя —
# при любой правке скрипта.
set -uo pipefail

if [ "$(id -u)" -ne 0 ]; then exec sudo bash "$0" "$@"; fi

die() { echo "::error::операция отменена: $*"; exit 1; }

ROOT=/opt/helm-rescue-checkpoints

echo "############ 0. НОВАЯ ТОЧКА ВОССТАНОВЛЕНИЯ ПОДТВЕРЖДЕНА ############"
# Удалять старый откат можно только когда новый доказан. Здесь это не
# «свежесть» в часах, а порядок событий: и бэкап, и тест восстановления
# обязаны быть НОВЕЕ самой старой точки, которую мы собираемся убрать.
for m in last-backup last-restore-test; do
  [ -f "/var/lib/helm-guardian/$m" ] || die "нет отметки $m"
  echo "  $m: $(stat -c %y "/var/lib/helm-guardian/$m")"
done
backup_at=$(stat -c %Y /var/lib/helm-guardian/last-backup)
restore_at=$(stat -c %Y /var/lib/helm-guardian/last-restore-test)

echo
echo "############ 1. ЧТО НАЙДЕНО ############"
found=0; removed=0; kept=0
for dir in "$ROOT"/*/; do
  [ -d "$dir" ] || continue
  manifest="$dir/MANIFEST.txt"
  if [ ! -f "$manifest" ]; then
    echo "  $dir — без манифеста, не трогаем"
    kept=$((kept + 1))
    continue
  fi

  public_chunks=$(awk -F': *' '/^public\.knowledge_chunks:/ {print $2}' "$manifest")
  taken_at=$(stat -c %Y "$dir")
  if [ -z "$public_chunks" ]; then
    echo "  $dir — в манифесте нет строки про public.knowledge_chunks, не трогаем"
    kept=$((kept + 1))
    continue
  fi

  if [ "$public_chunks" = "0" ]; then
    echo "  $dir — post-R1 (public.knowledge_chunks: 0), оставляем"
    kept=$((kept + 1))
    continue
  fi

  found=$((found + 1))
  echo "  $dir — PRE-R1: public.knowledge_chunks: $public_chunks"
  if [ "$backup_at" -le "$taken_at" ] || [ "$restore_at" -le "$taken_at" ]; then
    echo "    новая точка восстановления НЕ новее этой — оставляем"
    kept=$((kept + 1))
    continue
  fi
  rm -rf "$dir"
  removed=$((removed + 1))
  echo "    удалена"
done

echo
echo "############ 2. ПОСЛЕ ############"
echo "  найдено pre-R1: $found, удалено: $removed, оставлено: $kept"
left=$(ls -1d "$ROOT"/*/ 2>/dev/null | wc -l)
echo "  точек возврата осталось: $left"

# Хотя бы одна точка возврата обязана остаться: иначе следующий выкат
# пойдёт без быстрого отката, и мы сами себе сделаем то, от чего вся эта
# политика защищает.
[ "$left" -gt 0 ] || die "не осталось ни одной локальной точки возврата"

still_pre=0
for dir in "$ROOT"/*/; do
  [ -f "$dir/MANIFEST.txt" ] || continue
  n=$(awk -F': *' '/^public\.knowledge_chunks:/ {print $2}' "$dir/MANIFEST.txt")
  [ "${n:-0}" = "0" ] || still_pre=$((still_pre + 1))
done
echo "  pre-R1 точек осталось: $still_pre (ожидается 0)"
[ "$still_pre" = "0" ] || die "$still_pre pre-R1 точек остались на диске"

echo
echo "############ ГОТОВО ############"
