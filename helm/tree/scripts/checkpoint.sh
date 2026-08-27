#!/usr/bin/env bash
# Фазовый чекпоинт первичного развёртывания (ТЗ §31.0).
#
# Это локальный механизм отката до появления Forgejo. Git здесь не
# используется намеренно: §31.0 прямо запрещает делать Git prerequisite или
# фазовым гейтом первичной установки.
#
# Главное правило файла: в архив НЕ попадают plaintext-секреты. Для
# /etc/helm/secrets сохраняются только имена и контрольные суммы — этого
# хватает, чтобы понять «что изменилось», и недостаточно, чтобы утечь.
#
#   checkpoint.sh create P2 "перед миграцией схемы"
#   checkpoint.sh list
#   checkpoint.sh verify P2-20260827T143000

set -euo pipefail

ROOT=/opt/helm-state/implementation/checkpoints
SECRET_DIRS=(/etc/helm/secrets /etc/helm/ssh /etc/helm/backup /etc/signalai)
TRACKED=(/opt/helm /etc/caddy)

die() { echo "checkpoint: $*" >&2; exit 1; }

create() {
  local phase="${1:?нужна фаза, например P2}"
  local note="${2:-}"
  local stamp; stamp=$(date -u +%Y%m%dT%H%M%SZ)
  local dir="$ROOT/${phase}-${stamp}"

  mkdir -p "$dir"
  chmod 700 "$dir"

  # 1. Метаданные секретов вместо самих секретов.
  {
    echo "# Только имена, права и контрольные суммы. Значений здесь нет."
    for secret_dir in "${SECRET_DIRS[@]}"; do
      [[ -d "$secret_dir" ]] || continue
      find "$secret_dir" -type f -printf '%p\t%m\t%u:%g\t' -exec sha256sum {} \; \
        | awk '{print $1"\t"$2"\t"$3"\t"$4}'
    done
  } > "$dir/secrets-manifest.tsv"
  chmod 600 "$dir/secrets-manifest.tsv"

  # 2. Рабочее дерево без секретов. --exclude идёт ДО путей: иначе GNU tar
  #    применит его не ко всем аргументам.
  local existing=()
  for path in "${TRACKED[@]}"; do [[ -e "$path" ]] && existing+=("$path"); done
  [[ ${#existing[@]} -gt 0 ]] || die "нечего сохранять: ${TRACKED[*]} не существуют"

  # Компрессор выбирается по наличию. Молча остаться без архива нельзя:
  # пустой чекпоинт хуже отсутствующего — на него понадеются при откате.
  local archive compressor
  if command -v zstd >/dev/null 2>&1; then
    archive="$dir/files.tar.zst"; compressor=(-I 'zstd -3')
  elif command -v gzip >/dev/null 2>&1; then
    archive="$dir/files.tar.gz";  compressor=(-z)
  else
    archive="$dir/files.tar";     compressor=()
  fi

  # Ошибки tar НЕ подавляются: отсутствующий компрессор или нечитаемый путь
  # обязаны остановить создание чекпоинта, а не уехать в /dev/null.
  tar --exclude='*/secrets' --exclude='*/secrets/*' \
      --exclude='*/node_modules/*' \
      --exclude='*/.venv/*' \
      --exclude='*/__pycache__/*' \
      --exclude='*/dist/*' \
      "${compressor[@]}" -cf "$archive" "${existing[@]}" \
    || { rm -rf "$dir"; die "tar завершился с ошибкой — чекпоинт не создан"; }

  # Проверяем по существу, а не по размеру: размер зависит от содержимого,
  # и любой порог либо пропустит пустышку, либо забракует маленькое дерево.
  # Единственный честный вопрос — попал ли в архив каждый отслеживаемый путь.
  local listing; listing=$(tar -tf "$archive")
  for path in "${existing[@]}"; do
    if ! grep -q "^${path#/}" <<< "$listing"; then
      rm -rf "$dir"
      die "путь $path не попал в архив — чекпоинт не создан"
    fi
  done

  # 3. Дамп БД, если Postgres поднят. Права 0600 сразу, не потом.
  if command -v pg_dump >/dev/null && pg_isready -q 2>/dev/null; then
    mkdir -p "$dir/db_dump"
    ( umask 077; pg_dump -Fc helm > "$dir/db_dump/helm.dump" )
  fi

  # 4. Манифест и контрольные суммы.
  cat > "$dir/manifest.json" <<JSON
{
  "phase": "$phase",
  "created_at": "$(date -uIs)",
  "note": $(printf '%s' "${note:-}" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),
  "host": "$(hostname)",
  "tracked": [$(printf '"%s",' "${existing[@]}" | sed 's/,$//')],
  "secrets_included": false
}
JSON
  ( cd "$dir" && sha256sum files.tar* secrets-manifest.tsv manifest.json \
       db_dump/* 2>/dev/null > sha256.txt || true )

  # 5. Проверка: секрет не должен был попасть в архив.
  #    Записи-директории (с завершающим «/») отбрасываются: tar кладёт саму
  #    директорию даже когда её содержимое исключено, и без этого фильтра
  #    проверка падала бы на пустой папке secrets/.
  if tar -tf "$archive" | grep -v '/$' | grep -qE '/secrets/|\.env$|_key$|\.pem$'; then
    rm -rf "$dir"
    die "в архив попал секрет — чекпоинт удалён, разберитесь с исключениями"
  fi

  echo "$dir"
}

list() {
  [[ -d "$ROOT" ]] || { echo "чекпоинтов нет"; return; }
  for dir in "$ROOT"/*/; do
    [[ -f "$dir/manifest.json" ]] || continue
    python3 -c "
import json,sys
m=json.load(open('$dir/manifest.json'))
print(f\"{m['phase']:>6}  {m['created_at']}  {m.get('note','')}\")"
  done
}

verify() {
  local name="${1:?нужно имя чекпоинта}"
  local dir="$ROOT/$name"
  [[ -d "$dir" ]] || die "нет чекпоинта $name"
  ( cd "$dir" && sha256sum -c sha256.txt --quiet ) && echo "$name: контрольные суммы совпадают"
  local archive; archive=$(ls "$dir"/files.tar* 2>/dev/null | head -1)
  [[ -n "$archive" ]] || die "$name: архива нет"
  tar -tf "$archive" >/dev/null && echo "$name: архив читается"
}

case "${1:-}" in
  create) shift; create "$@" ;;
  list)   list ;;
  verify) shift; verify "$@" ;;
  *)      die "использование: checkpoint.sh {create <фаза> [примечание]|list|verify <имя>}" ;;
esac
