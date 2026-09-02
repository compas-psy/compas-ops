#!/bin/bash
# HELM v4.0 RESCUE · R1, необратимая часть: удалить копию health-чанков
# из общей схемы (§14.16).
#
# Запускается ТОЛЬКО через action=destructive: `recon` отказывается
# выполнять destructive-*.sh по имени, а сам destructive проходит гейт
# workflow (свежий offsite-бэкап И пройденный restore-тест, либо
# подтверждённый владельцем снапшот провайдера).
#
# Гейт workflow — не единственная защита. Скрипт заново проверяет всё,
# на чём держится безопасность удаления, и отказывается при первом
# несовпадении. Причина простая: гейт отвечает на вопрос «есть ли куда
# откатиться», а проверки ниже — на вопрос «а уцелели ли данные в новом
# месте». Второй вопрос важнее: откат нужен только если ответ «нет».
set -uo pipefail

if [ "$(id -u)" -ne 0 ]; then exec sudo bash "$0" "$@"; fi

die() { echo "::error::операция отменена: $*"; exit 1; }
psql() { docker exec helm-postgres-1 psql -U helm -d helm -v ON_ERROR_STOP=1 "$@"; }

WORK=$(mktemp -d /var/lib/helm-guardian/drop-public-health-XXXXXX)
trap 'rm -rf "$WORK"' EXIT

#: Любой отказ SQL фатален. Молча вернуть пустоту — это то, как ошибка в
#: имени колонки превращается в зелёную проверку (прогон #152).
q() {
  if ! psql -tAc "$1" > "$WORK/q.out" 2> "$WORK/q.err"; then
    echo "::error::запрос не выполнился:" >&2
    sed 's/^/    /' "$WORK/q.err" >&2
    exit 1
  fi
  cat "$WORK/q.out"
}

PRIVATE=/opt/helm-knowledge-private

echo "############ 0. СТРАХОВКА ############"
# Те же пороги, что в гейте workflow: скрипт обязан быть безопасен и при
# запуске руками, а не только через кнопку с гейтом.
fresh() {
  [ -f "$1" ] || die "нет отметки $1"
  age=$(( ( $(date +%s) - $(stat -c %Y "$1") ) / 3600 ))
  echo "  $1: $age ч назад (предел $2 ч)"
  [ "$age" -le "$2" ] || die "$1 старше $2 ч"
}
fresh /var/lib/helm-guardian/last-backup 24
fresh /var/lib/helm-guardian/last-restore-test 168

# Проверка, которой в общем гейте быть не может: бэкап обязан быть СВЕЖЕЕ
# приватного дерева. Снапшот, снятый до миграции, формально свежий, но
# приватных файлов в нём нет по построению — восстановление из него
# вернуло бы систему БЕЗ тех данных, которые мы сейчас убираем из общей
# схемы. Ровно тот случай, когда страховка выглядит целой и не работает.
backup_at=$(stat -c %Y /var/lib/helm-guardian/last-backup)
private_at=$(stat -c %Y "$PRIVATE")
echo "  бэкап:            $(date -d @"$backup_at" '+%F %T')"
echo "  приватное дерево: $(date -d @"$private_at" '+%F %T')"
[ "$backup_at" -gt "$private_at" ] \
  || die "последний бэкап старше приватного дерева — приватных файлов в нём нет"

echo
echo "############ 1. СВОЯ ТОЧКА ВОЗВРАТА ############"
/opt/helm/scripts/local-rescue-checkpoint.sh create >/dev/null \
  || die "локальная точка возврата не снялась"
echo "  снята"

echo
echo "############ 2. ДАННЫЕ ЦЕЛЫ В НОВОМ МЕСТЕ ############"
divergent=$(q "
  with p as (
    select c.source_id, count(*) n, count(c.embedding) v,
           md5(string_agg(c.text, E'\n' order by c.ordinal)) d
    from knowledge_chunks c join knowledge_sources s on s.id = c.source_id
    where s.domain = 'health' group by c.source_id),
  h as (
    select source_id, count(*) n, count(embedding) v,
           md5(string_agg(text, E'\n' order by ordinal)) d
    from health.knowledge_chunks group by source_id)
  select count(*)
  from p full outer join h on h.source_id = p.source_id
  where p.n is distinct from h.n or p.v is distinct from h.v
     or p.d is distinct from h.d")
echo "  источников с расхождением: $divergent (ожидается 0)"
[ "$divergent" = "0" ] || die "$divergent источников разошлись между схемами"

expected=$(q "
  select count(*) from knowledge_chunks c
  join knowledge_sources s on s.id = c.source_id
  where s.domain = 'health'")
in_health=$(q "select count(*) from health.knowledge_chunks")
echo "  копия в public: $expected, оригинал в health: $in_health"
[ "$expected" -gt 0 ] || die "в public нет health-чанков — убирать нечего"
[ "$expected" = "$in_health" ] || die "числа не сходятся: $expected против $in_health"

echo
echo "############ 3. ФАЙЛЫ ЦЕЛЫ НА ПРИВАТНЫХ ПУТЯХ ############"
# ИСПРАВЛЕНО 02.09.2026 по замечанию владельца. Первая версия спрашивала
# `stored_path` и `sha256` у `health.knowledge_source_private`. Таких
# колонок там нет: сайдкар держит `source_id`, `knowledge_user_id`,
# `original_filename`, `parse_error`, `created_at` — то есть ровно
# чувствительное, и ничего больше. Путь и хэш файла живут в публичном
# конверте `knowledge_sources` (`raw_path`, `source_path`, `sha256`), и
# правильный запрос уже был написан в `r1-verify.sh` — здесь он был
# переписан заново и переписан неверно.
#
# Читаем в файл, а не через подстановку процессов: там код возврата psql
# основной оболочке не виден, и запрос с несуществующей колонкой дал бы
# ноль строк, ноль пропаж и зелёную проверку. Именно так этот дефект и
# проявился в тесте восстановления (прогон #152).
q "
  select s.id || E'\t' || s.sha256 || E'\t' || s.raw_path || E'\t' || coalesce(s.source_path, '')
  from knowledge_sources s
  where s.domain = 'health'
  order by s.id" > "$WORK/files.tsv" || die "не удалось прочитать пути файлов"

raw_ok=0; raw_missing=0; raw_mismatch=0
l1_ok=0; l1_missing=0; l1_absent=0
outside=0; no_sidecar=0

while IFS=$'\t' read -r sid sha raw src; do
  [ -n "$sid" ] || continue

  # Принадлежность сайдкару: конверт без приватной строки означает, что
  # источник мигрирован не полностью, и снимать его копию нельзя.
  if [ "$(q "select count(*) from health.knowledge_source_private where source_id = '$sid'")" != "1" ]; then
    no_sidecar=$((no_sidecar+1)); echo "  НЕТ САЙДКАРА: $sid"
  fi

  case "$raw" in "$PRIVATE"/*) ;; *) outside=$((outside+1)); echo "  ВНЕ ПРИВАТНОГО ДЕРЕВА: $raw" ;; esac
  if [ ! -f "$raw" ]; then
    raw_missing=$((raw_missing+1)); echo "  НЕТ ОРИГИНАЛА: $raw"
  elif [ "$(sha256sum "$raw" | cut -d' ' -f1)" != "$sha" ]; then
    raw_mismatch=$((raw_mismatch+1)); echo "  ХЭШ РАЗОШЁЛСЯ: $raw"
  else
    raw_ok=$((raw_ok+1))
  fi

  if [ -z "$src" ]; then
    l1_absent=$((l1_absent+1)); echo "  НЕТ source_path В БАЗЕ: $sid"
  else
    case "$src" in "$PRIVATE"/*) ;; *) outside=$((outside+1)); echo "  ВНЕ ПРИВАТНОГО ДЕРЕВА: $src" ;; esac
    if [ -f "$src" ]; then l1_ok=$((l1_ok+1)); else
      l1_missing=$((l1_missing+1)); echo "  НЕТ КОНСПЕКТА: $src"
    fi
  fi
done < "$WORK/files.tsv"

envelopes=$(wc -l < "$WORK/files.tsv")
echo "  источников $envelopes: оригиналов $raw_ok, конспектов $l1_ok"
echo "  нет оригинала $raw_missing, хэш разошёлся $raw_mismatch, нет конспекта $l1_missing,"
echo "  без source_path $l1_absent, вне приватного дерева $outside, без сайдкара $no_sidecar"

[ "$envelopes" -gt 0 ]            || die "health-источников ноль — проверять нечего"
[ "$raw_ok" = "$envelopes" ]      || die "оригиналов целых $raw_ok из $envelopes"
[ "$l1_ok" = "$envelopes" ]       || die "конспектов целых $l1_ok из $envelopes"
[ "$raw_missing" = "0" ]          || die "$raw_missing оригиналов не найдено"
[ "$raw_mismatch" = "0" ]         || die "$raw_mismatch файлов не совпали по sha256"
[ "$l1_missing" = "0" ]           || die "$l1_missing конспектов не найдено"
[ "$l1_absent" = "0" ]            || die "$l1_absent источников без source_path"
[ "$outside" = "0" ]              || die "$outside путей вне приватного дерева"
[ "$no_sidecar" = "0" ]           || die "$no_sidecar источников без приватного сайдкара"

echo
echo "############ 4. СНЯТИЕ КОПИИ ############"
# Всё внутри одного блока: и пересчёт ожидаемого, и сверка с health-схемой,
# и сама операция, и проверка результата. Исключение внутри блока
# откатывает его целиком, поэтому «сняли половину и упали» невозможно.
#
# Числа считаются здесь заново, а не передаются из шагов выше: между
# проверкой и операцией мог пройти ingest, и работать надо по тому
# состоянию, которое есть в момент операции, а не по прочитанному минуту
# назад. Проверки выше — про «можно ли вообще», этот блок — про «сколько
# ровно сейчас».
psql -v ON_ERROR_STOP=1 <<'SQL' || die "транзакция не прошла"
do $$
declare
  want bigint;
  mirrored bigint;
  got bigint;
begin
  select count(*) into want
    from knowledge_chunks c join knowledge_sources s on s.id = c.source_id
   where s.domain = 'health';
  select count(*) into mirrored from health.knowledge_chunks;

  if want <> mirrored then
    raise exception 'в public % строк, в health % — копию снимать нельзя',
      want, mirrored;
  end if;

  with d as (
    delete from knowledge_chunks c
    using knowledge_sources s
    where s.id = c.source_id and s.domain = 'health'
    returning c.id
  )
  select count(*) into got from d;

  if got <> want then
    raise exception 'затронуто % строк вместо % — откат', got, want;
  end if;

  raise notice 'снято строк: %', got;
end $$;
SQL

echo
echo "############ 5. ПОСЛЕ ############"
after_public=$(q "
  select count(*) from knowledge_chunks c
  join knowledge_sources s on s.id = c.source_id
  where s.domain = 'health'")
after_health=$(q "select count(*) from health.knowledge_chunks")
echo "  health-чанков в public: $after_public (ожидается 0)"
echo "  чанков в health:        $after_health (ожидается $in_health)"
[ "$after_public" = "0" ] || die "в public осталось $after_public health-чанков"
[ "$after_health" = "$in_health" ] || die "в health стало $after_health вместо $in_health"

echo
echo "############ ГОТОВО ############"
echo "Дальше обязательно: прогон r1-probe-smoke.sh — поиск должен отвечать"
echo "из health-схемы. Молчит — возврат из точки шага 1."
