#!/bin/bash
# HELM v4.0 RESCUE · R1: перенос файлов health-источников в приватное
# дерево (§14.16).
#
# Что переносится: L0-оригиналы (/opt/helm-knowledge/raw/health/*) и
# L1-конспекты (/opt/helm-knowledge/sources/<sha>.md, лежащие вперемешку
# с остальными доменами) в
# /opt/helm-knowledge-private/health/users/<user_id>/{raw/health,sources}.
#
# Файл и строка БД меняются ПАРОЙ на каждый источник: documents.py и
# worker.py читают путь из колонки буквально, и рассинхрон означает
# "у этой записи нет исходного файла" на скачивании и HEALTH_PARSE_FAILED
# на переразборе — без единой подсказки, что дело в переезде.
#
# Требует, чтобы приватное дерево уже существовало (knowledge-bootstrap.sh)
# и было примонтировано в контейнеры (docker-compose.yml) — иначе новые
# health-загрузки писать туда не смогут.
#
# Идемпотентен: источник, чьи пути уже указывают внутрь приватного дерева
# и чьи файлы там лежат, пропускается.
set -uo pipefail

PRIVATE=/opt/helm-knowledge-private
psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm "$@"; }

if ! sudo test -d "$PRIVATE"; then
  echo "ОТКАЗ: $PRIVATE не существует — сначала knowledge-bootstrap.sh"
  exit 1
fi

moved=0; skipped=0; failed=0

while IFS='|' read -r source_id user_id raw_path source_path; do
  [ -n "$source_id" ] || continue
  root="$PRIVATE/health/users/$user_id"

  new_raw="$root/raw/health/$(basename "$raw_path")"
  new_source="$root/sources/$(basename "$source_path")"

  if [ "$raw_path" = "$new_raw" ] && [ "$source_path" = "$new_source" ] \
     && sudo test -f "$new_raw" && sudo test -f "$new_source"; then
    skipped=$((skipped + 1))
    continue
  fi

  sudo mkdir -p "$root/raw/health" "$root/sources"

  ok=1
  for pair in "$raw_path|$new_raw" "$source_path|$new_source"; do
    old="${pair%%|*}"; new="${pair##*|}"
    if sudo test -f "$new"; then
      continue                      # уже на месте с прошлого прогона
    elif sudo test -f "$old"; then
      sudo mv "$old" "$new" || ok=0
    else
      echo "  $source_id: НЕТ ФАЙЛА ни в $old, ни в $new"
      ok=0
    fi
  done

  if [ "$ok" -ne 1 ]; then
    failed=$((failed + 1))
    continue
  fi

  psql -qtAc "update knowledge_sources
              set raw_path = '$new_raw', source_path = '$new_source'
              where id = '$source_id'" >/dev/null || { failed=$((failed + 1)); continue; }
  moved=$((moved + 1))
done < <(psql -tAc "
  select id, knowledge_user_id, raw_path, source_path
  from knowledge_sources where domain = 'health' order by id")

# Владелец и права — как у остального дерева: файлы, пришедшие из общего
# Vault, принесли бы с собой helm:helm и оказались бы читаемы шире, чем
# каталог, в который их положили.
sudo chown -R root:helm-health "$PRIVATE"
sudo chmod -R 2770 "$PRIVATE"

echo "готово: перенесено $moved, пропущено $skipped, отказов $failed"

echo
echo "--- что осталось в общем дереве от health ---"
echo "raw/health файлов:  $(sudo find /opt/helm-knowledge/raw/health -type f 2>/dev/null | wc -l)"
echo "sources .md файлов: $(sudo find /opt/helm-knowledge/sources -name '*.md' -type f 2>/dev/null | wc -l)"
echo "--- в приватном дереве ---"
sudo find "$PRIVATE" -maxdepth 5 -type d | sort
echo "файлов всего: $(sudo find "$PRIVATE" -type f | wc -l)"

exit $((failed > 0 ? 1 : 0))
