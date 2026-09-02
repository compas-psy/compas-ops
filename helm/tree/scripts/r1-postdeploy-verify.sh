#!/bin/bash
# HELM v4.0 RESCUE · R1: проверка после SAFE-выката новой политики
# (02.09.2026). Read-only, ничего не меняет.
#
# Порядок ровно тот, в котором отказ одного делает бессмысленной проверку
# следующего: контейнеры → приватное дерево ВНУТРИ них → данные →
# читаемость точки возврата. Живой поиск проверяется отдельным прогоном
# r1-probe-smoke.sh: он открывает транзакцию и её откатывает, смешивать
# это с read-only проверкой не стоит.
set -uo pipefail

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm "$@"; }

echo "############ 1. КОНТЕЙНЕРЫ ############"
sudo docker ps --format '{{.Names}}\t{{.Status}}' | sort

echo
echo "############ 2. ПРИВАТНОЕ ДЕРЕВО ВНУТРИ КОНТЕЙНЕРОВ ############"
# Смонтировано на хосте — не то же самое, что видно процессу: до 02.09
# каталога не было вовсе, и монтирование добавлено этим же выкатом.
# Проверяется и чтение, и запись: дерево 2770 root:helm-health, контейнер
# ходит туда по group_add, и «вижу, но не могу писать» — отказ, который
# проявится только при следующем ingest.
for c in helm-core helm-knowledge-worker; do
  echo "--- $c ---"
  sudo docker compose -f /opt/helm/compose/docker-compose.yml exec -T "$c" sh -c '
    d=/opt/helm-knowledge-private
    [ -d "$d" ] || { echo "  НЕ СМОНТИРОВАНО"; exit 1; }
    echo "  файлов: $(find "$d" -type f | wc -l)"
    echo "  id: $(id -u):$(id -g), группы: $(id -G)"
    t="$d/health/users/.write-probe.$$"
    if touch "$t" 2>/dev/null; then echo "  запись: да"; rm -f "$t"; else echo "  запись: НЕТ"; fi
  ' 2>&1 | grep -v '^time=' || echo "  контейнер не отвечает"
done

echo
echo "############ 3. ДАННЫЕ: СЧЁТЧИКИ И ОТПЕЧАТКИ ############"
# Ожидается: 953 = 953, отпечатки равны. Отпечаток — md5 от текстов всех
# чанков в одном порядке: совпал — перенесён текст, а не только строки.
psql -tA -F$'\t' -c "
  select 'public.knowledge_chunks', count(*),
         md5(string_agg(text, '' order by source_id, ordinal))
    from knowledge_chunks
  union all
  select 'health.knowledge_chunks', count(*),
         md5(string_agg(text, '' order by source_id, ordinal))
    from health.knowledge_chunks
  union all
  select 'эмбеддингов public/health',
         (select count(*) from knowledge_chunks where embedding is not null),
         (select count(*)::text from health.knowledge_chunks where embedding is not null)"

echo
echo "--- helm_app не читает health (ожидается f везде) ---"
psql -tA -F$'\t' -c "
  select c.relname,
         has_table_privilege('helm_app', 'health.' || c.relname, 'select')
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'health' and c.relkind = 'r'
   order by c.relname"

echo
echo "############ 4. ТОЧКА ВОЗВРАТА ЧИТАЕТСЯ ############"
# Не «файл существует», а перечитывание сумм и целости архивов: точка
# возврата, которую не проверили, — обещание, а не страховка.
sudo /opt/helm/scripts/local-rescue-checkpoint.sh verify

echo
echo "--- состав точки возврата ---"
sudo ls -lh "$(sudo ls -1d /opt/helm-rescue-checkpoints/*/ | sort | tail -1)"

echo
echo "--- отметки страховок ---"
# sudo обязателен: /var/lib/helm-guardian роли helm не читается, и без
# него проверка отвечает «отметки нет» на существующую отметку.
for m in last-local-checkpoint last-backup last-restore-test; do
  sudo stat -c "  $m: %y" "/var/lib/helm-guardian/$m" 2>/dev/null || echo "  $m: отметки нет"
done

echo
echo "############ ГОТОВО ############"
