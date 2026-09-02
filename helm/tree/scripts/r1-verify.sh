#!/bin/bash
# HELM v4.0 RESCUE · R1: приёмка переноса (§30.8.5 C). Read-only.
#
# Базовые значения, снятые ДО миграции (r1-preflight.sh, 02.09.2026):
#   health-источников                 90
#   чанков health в public            953
#   символов                          207 346
#   с эмбеддингом                     881
#   отпечаток текста (md5)            b31c1f092d0613b61f0e47c26f180749
set -uo pipefail

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm "$@"; }

echo "############ 1. САЙДКАР: строка нужна КАЖДОМУ источнику ############"
psql -c "
select count(*) as всего_health,
       count(*) filter (where original_filename is not null) as имя_ещё_в_public,
       (select count(*) from health.knowledge_source_private) as строк_в_сайдкаре,
       count(*) filter (where not exists (
         select 1 from health.knowledge_source_private p where p.source_id = s.id
       )) as без_сайдкара
from knowledge_sources s where s.domain = 'health'"

echo
echo "############ 2. ЧАНКИ: public против health ############"
psql -c "
select 'public' as где, count(*) as чанков, count(c.embedding) as с_вектором,
       sum(length(c.text)) as символов,
       md5(string_agg(c.text, E'\n' order by c.source_id, c.ordinal)) as отпечаток
from knowledge_chunks c join knowledge_sources s on s.id = c.source_id
where s.domain = 'health'"
psql -c "
select 'health' as где, count(*) as чанков, count(embedding) as с_вектором,
       sum(length(text)) as символов,
       md5(string_agg(text, E'\n' order by source_id, ordinal)) as отпечаток
from health.knowledge_chunks"

echo
echo "############ 3. ИСТОЧНИКИ, ГДЕ ОТПЕЧАТКИ РАЗОШЛИСЬ (ожидается пусто) ############"
psql -c "
with p as (
  select c.source_id, count(*) n, count(c.embedding) v,
         md5(string_agg(c.text, E'\n' order by c.ordinal)) d
  from knowledge_chunks c join knowledge_sources s on s.id = c.source_id
  where s.domain = 'health' group by c.source_id),
h as (
  select source_id, count(*) n, count(embedding) v,
         md5(string_agg(text, E'\n' order by ordinal)) d
  from health.knowledge_chunks group by source_id)
select coalesce(p.source_id, h.source_id) as source_id,
       p.n as public_чанков, h.n as health_чанков,
       p.v as public_векторов, h.v as health_векторов,
       (p.d is not distinct from h.d) as отпечаток_совпал
from p full outer join h on h.source_id = p.source_id
where p.n is distinct from h.n or p.v is distinct from h.v
   or p.d is distinct from h.d"

echo
echo "############ 4. helm_app НЕ ЧИТАЕТ health (ожидается f везде) ############"
psql -c "
select t as таблица, has_table_privilege('helm_app', 'health.' || t, 'SELECT') as может_читать
from unnest(array['knowledge_source_private','knowledge_chunks',
                  'knowledge_relations','knowledge_notes']) as t"

echo
echo "############ 5. ПРИВАТНОЕ ДЕРЕВО ############"
for d in /opt/helm-knowledge-private /opt/helm-knowledge/raw/health /opt/helm-knowledge/sources; do
  if sudo test -d "$d"; then
    printf '%-42s %s, файлов %s\n' "$d" \
      "$(sudo stat -c '%U:%G %a' "$d")" "$(sudo find "$d" -type f 2>/dev/null | wc -l)"
  else
    printf '%-42s нет\n' "$d"
  fi
done

echo
echo "############ 6. ФАЙЛЫ НА НОВЫХ ПУТЯХ ЦЕЛЫ ############"
# Ровно то, что делает documents.py::read_original() перед выдачей: файл
# на пути из БД существует и его sha256 совпадает с записанной. Если
# перенос разошёлся с колонкой, владелец увидит "у этой записи нет
# исходного файла" — сообщение, по которому причину не восстановить.
missing=0; mismatch=0; ok=0
while IFS='|' read -r sha raw; do
  [ -n "$sha" ] || continue
  if ! sudo test -f "$raw"; then
    missing=$((missing + 1)); echo "  НЕТ ФАЙЛА: $raw"
  elif [ "$(sudo sha256sum "$raw" | cut -d' ' -f1)" != "$sha" ]; then
    mismatch=$((mismatch + 1)); echo "  ХЭШ РАЗОШЁЛСЯ: $raw"
  else
    ok=$((ok + 1))
  fi
done < <(psql -tAc "select sha256, raw_path from knowledge_sources where domain = 'health'")
echo "  целых $ok, отсутствует $missing, хэш разошёлся $mismatch"
echo "--- пути ещё указывающие в общее дерево (ожидается 0) ---"
psql -tAc "
select count(*) from knowledge_sources
where domain = 'health'
  and (raw_path like '/opt/helm-knowledge/%' or source_path like '/opt/helm-knowledge/%')"

echo
echo "############ 7. ТОЧКА ВОЗВРАТА ############"
# backup.sh трогает этот файл ТОЛЬКО после успешного снапшота — это
# единственная отметка, по которой видно, снялась ли она на самом деле.
stat -c 'последний успешный бэкап: %y' /var/lib/helm-guardian/last-backup 2>/dev/null \
  || echo "отметки об успешном бэкапе нет"

echo
echo "############ ГОТОВО ############"
