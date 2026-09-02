#!/bin/bash
# HELM v4.0 RESCUE · R1 preflight. Read-only.
#
# Три вещи, без которых план миграции health-долга строится на догадках:
#
# 1. Подхвачен ли контейнерами HELM_HEALTH_DATABASE_URL. От этого зависит
#    ВСЁ: health_schema_configured() читается один раз при старте процесса
#    (@lru_cache), и если DSN не виден — после переноса probe() перестанет
#    искать в health-схеме молча, без ошибки, и корпус погаснет.
# 2. Есть ли health-источники без original_filename. Такие не попадают в
#    выборку migrate-health-filenames-to-sidecar.sh, но сайдкар-строка им
#    всё равно нужна: health.knowledge_chunks.source_id ссылается на неё
#    внешним ключом.
# 3. Сколько на самом деле весит health на диске и сколько места есть.
set -uo pipefail

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm "$@"; }

echo "############ 1. DSN health ВНУТРИ КОНТЕЙНЕРОВ ############"
cd /opt/helm/compose || exit 1
for svc in helm-core helm-knowledge-worker; do
  echo "--- $svc ---"
  sudo docker compose exec -T "$svc" sh -c '
    if [ -n "${HELM_HEALTH_DATABASE_URL:-}" ]; then
      echo "HELM_HEALTH_DATABASE_URL: задан напрямую"
    elif [ -n "${HELM_HEALTH_DATABASE_URL_FILE:-}" ]; then
      if [ -s "$HELM_HEALTH_DATABASE_URL_FILE" ]; then
        echo "секрет $HELM_HEALTH_DATABASE_URL_FILE: непустой, $(wc -c < "$HELM_HEALTH_DATABASE_URL_FILE") байт"
        grep -o "search_path=[^ ]*" "$HELM_HEALTH_DATABASE_URL_FILE" || echo "  (search_path в DSN не найден)"
      else
        echo "секрет $HELM_HEALTH_DATABASE_URL_FILE: ПУСТ или отсутствует"
      fi
    else
      echo "ни переменной, ни файла секрета — health-схема процессу не видна"
    fi' 2>&1 | tail -5
  echo "--- что видит сам код ---"
  sudo docker compose exec -T "$svc" python3 -c "
from helm_core.knowledge.health_schema import health_schema_configured
print('health_schema_configured() =', health_schema_configured())
" 2>&1 | tail -3
done

echo
echo "############ 2. ПОЛНОТА САЙДКАРА И ИМЁН ############"
psql -c "
select count(*) as всего_health,
       count(*) filter (where original_filename is not null) as имя_в_public,
       count(*) filter (where original_filename is null)     as имя_отсутствует_в_public,
       count(*) filter (where raw_path is not null)          as есть_raw_path,
       count(*) filter (where source_path is not null)       as есть_source_path
from knowledge_sources where domain = 'health'"
psql -c "
select count(*) as строк_в_сайдкаре,
       count(*) filter (where original_filename is not null) as с_именем
from health.knowledge_source_private"
echo "--- источники БЕЗ сайдкар-строки (им всё равно нужна строка ради внешнего ключа) ---"
psql -c "
select count(*) as без_сайдкара
from knowledge_sources s
where s.domain = 'health'
  and not exists (select 1 from health.knowledge_source_private p where p.source_id = s.id)"

echo
echo "############ 3. КОЛОНКИ, КОТОРЫХ НЕТ В HEALTH-ЗЕРКАЛЕ ############"
psql -c "
select count(*) filter (where c.page is not null)          as page_заполнен,
       count(*) filter (where c.time_start_ms is not null) as time_start_заполнен,
       count(*) filter (where c.time_end_ms is not null)   as time_end_заполнен
from knowledge_chunks c join knowledge_sources s on s.id = c.source_id
where s.domain = 'health'"

echo
echo "############ 4. ХЭШ-БАЗА ДЛЯ СВЕРКИ ПОСЛЕ ПЕРЕНОСА ############"
echo "--- общий отпечаток текста всех health-чанков (public) ---"
psql -tAc "
select md5(string_agg(c.text, E'\n' order by c.source_id, c.ordinal))
from knowledge_chunks c join knowledge_sources s on s.id = c.source_id
where s.domain = 'health'"
echo "--- по источникам: id, чанков, символов, отпечаток (первые 10) ---"
psql -c "
select c.source_id, count(*) as чанков, sum(length(c.text)) as символов,
       count(c.embedding) as с_вектором,
       left(md5(string_agg(c.text, E'\n' order by c.ordinal)), 12) as отпечаток
from knowledge_chunks c join knowledge_sources s on s.id = c.source_id
where s.domain = 'health'
group by c.source_id order by c.source_id limit 10"

echo
echo "############ 5. ФАЙЛЫ HEALTH НА ДИСКЕ ############"
echo "--- raw/health ---"
sudo du -sh /opt/helm-knowledge/raw/health 2>/dev/null || echo "каталога нет"
echo "    файлов: $(sudo find /opt/helm-knowledge/raw/health -type f 2>/dev/null | wc -l)"
echo "--- sources (общие, health вперемешку) ---"
sudo du -sh /opt/helm-knowledge/sources 2>/dev/null
echo "    файлов .md: $(sudo find /opt/helm-knowledge/sources -name '*.md' -type f 2>/dev/null | wc -l)"
echo "--- права и владелец ---"
sudo stat -c '%n %U:%G %a' /opt/helm-knowledge /opt/helm-knowledge/raw/health /opt/helm-knowledge/sources 2>/dev/null
echo "--- место ---"
df -h / | tail -1
echo "--- группы, в которых состоит контейнерный пользователь воркера ---"
sudo docker compose exec -T helm-knowledge-worker id 2>&1 | tail -2

echo
echo "############ ГОТОВО ############"
