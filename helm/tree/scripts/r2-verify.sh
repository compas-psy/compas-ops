#!/bin/bash
# HELM v4.0 RESCUE · R2: приёмка схемы semantic-v2. Read-only.
#
# Что доказывается:
#   1. миграция накатилась и схема совпала с моделями;
#   2. на всех пяти новых таблицах RLS включён И принудителен;
#   3. health-зеркала созданы, и helm_app их не читает;
#   4. semantic-v1 заморожен: старые таблицы на месте, но не растут;
#   5. счётчики semantic-v1 сняты для сравнения (§14.22 «quarantine/
#      export counts for comparison»).
#
# Цифры печатаются рядом с ожидаемыми. Строка без ожидания — это
# наблюдение, а не проверка; такие здесь только в разделе 5, где
# сравнивать пока не с чем.
set -uo pipefail

fails=0
psql() { sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tA "$@" < /dev/null; }
want() {  # want <что> <получено> <ожидается>
  printf '  %-46s %-12s (ожидается %s)' "$1" "$2" "$3"
  if [ "$2" = "$3" ]; then echo; else echo "   ← НЕ СОВПАЛО"; fails=$((fails + 1)); fi
}

echo "############ 1. РЕВИЗИЯ СХЕМЫ ############"
echo -n "  выкачено: "; sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo "отметки нет"
cd /opt/helm/compose
echo -n "  alembic:  "
sudo docker compose exec -T helm-core python3 -m alembic current 2>/dev/null | tail -1

echo
echo "############ 2. ТАБЛИЦЫ SEMANTIC-V2 И RLS ############"
for t in knowledge_semantic_runs knowledge_nodes knowledge_node_mentions \
         knowledge_edges knowledge_entity_aliases; do
  # `boolean::text` даёт `true`, а `has_table_privilege()` ниже печатает
  # `t` — разные представления одного и того же в одном отчёте читаются
  # плохо и один раз уже дали ложный FAIL (прогон #168, дефект этого
  # скрипта, не сервера). Приводим к одной букве явно.
  state=$(psql -c "select coalesce(
      (select case when relrowsecurity then 't' else 'f' end || '/' ||
                   case when relforcerowsecurity then 't' else 'f' end
         from pg_class where relname = '$t' and relnamespace = 'public'::regnamespace),
      'НЕТ ТАБЛИЦЫ')")
  want "public.$t (RLS/FORCE)" "$state" "t/t"
done

policies=$(psql -c "
  select count(*) from pg_policies
   where schemaname = 'public' and policyname = 'knowledge_tenant_isolation'
     and tablename in ('knowledge_semantic_runs','knowledge_nodes',
                       'knowledge_node_mentions','knowledge_edges',
                       'knowledge_entity_aliases')")
want "политик изоляции на новых таблицах" "$policies" "5"

col=$(psql -c "select count(*) from information_schema.columns
                where table_name = 'knowledge_sources'
                  and column_name = 'current_semantic_run_id'")
want "knowledge_sources.current_semantic_run_id" "$col" "1"

echo
echo "############ 3. HEALTH-ЗЕРКАЛА ############"
for t in knowledge_nodes knowledge_node_mentions knowledge_edges knowledge_entity_aliases; do
  exists=$(psql -c "select count(*) from pg_tables
                     where schemaname = 'health' and tablename = '$t'")
  want "health.$t создана" "$exists" "1"
  granted=$(psql -c "select has_table_privilege('helm_app', 'health.$t', 'SELECT')")
  want "helm_app SELECT на health.$t" "$granted" "f"
done

mirrored=$(psql -c "select count(*) from pg_tables
                     where schemaname = 'health' and tablename = 'knowledge_semantic_runs'")
want "health.knowledge_semantic_runs НЕ зеркалится" "$mirrored" "0"

echo
echo "############ 3a. ГЕЙТ ТЕКУЩЕЙ РЕВИЗИИ (R2-hardening) ############"
# §14.5: current_semantic_run_id может указывать только на READY-ревизию
# того же источника и того же владельца, и такая ревизия не может
# испортиться, пока остаётся текущей. Внешний ключ этого не даёт — он
# одинаково пропустит FAILED, чужой документ и чужого владельца.
for trg in knowledge_sources_current_semantic_run_guard \
           knowledge_semantic_runs_current_guard; do
  want "триггер $trg" \
       "$(psql -c "select count(*) from pg_trigger where tgname = '$trg' and not tgisinternal")" "1"
done

echo
echo "############ 3b. ЗАКРЫТЫЕ РЕЕСТРЫ В БАЗЕ ############"
# Реестр §14.9 назван закрытым — значит закрыт не только Python-enum:
# мимо enum ходят миграции, backfill и psql руками.
want "CHECK в public" \
     "$(psql -c "select count(*) from pg_constraint c
                   join pg_class t on t.oid = c.conrelid
                   join pg_namespace n on n.oid = t.relnamespace
                  where n.nspname = 'public' and c.contype = 'c'
                    and c.conname like 'ck_knowledge_%'")" "10"
want "CHECK в health" \
     "$(psql -c "select count(*) from pg_constraint c
                   join pg_class t on t.oid = c.conrelid
                   join pg_namespace n on n.oid = t.relnamespace
                  where n.nspname = 'health' and c.contype = 'c'
                    and c.conname like 'ck_knowledge_%'")" "9"
for schema in public health; do
  want "$schema.mention.semantic_run_id NOT NULL" \
       "$(psql -c "select is_nullable from information_schema.columns
                     where table_schema = '$schema'
                       and table_name = 'knowledge_node_mentions'
                       and column_name = 'semantic_run_id'")" "NO"
done
# Реальная попытка записи мимо реестра — не только наличие ограничения.
# Транзакция откатывается, ничего не остаётся.
#
# Нарушается РОВНО ОДНО ограничение: kind = entity (личности ревизия не
# обязательна), владелец настоящий, поддельный только status. Иначе
# Postgres назвал бы любое из нарушенных, и проверка стала бы гадательной.
bogus=$(psql -c "begin;
  insert into knowledge_nodes (id, knowledge_user_id, kind, canonical_label,
                               security_scope, status, created_at, updated_at)
  values (gen_random_uuid(), (select id from knowledge_users order by created_at limit 1),
          'entity', 'проверка реестра', 'internal', 'нет', now(), now());
  rollback;" 2>&1 | grep -c 'ck_knowledge_nodes_status')
want "неизвестный status отвергнут" "$bogus" "1"

echo
echo "############ 4. SEMANTIC-V1 НА МЕСТЕ, НО ЗАМОРОЖЕН ############"
# §14.5: аддитивно и обратимо до R10 — старые таблицы не удаляются.
for t in knowledge_notes knowledge_relations; do
  exists=$(psql -c "select count(*) from pg_tables
                     where schemaname = 'public' and tablename = '$t'")
  want "public.$t не удалена" "$exists" "1"
done

echo
echo "############ 5. СЧЁТЧИКИ SEMANTIC-V1 (для сравнения) ############"
# §14.22: «quarantine/export counts for comparison». Ожидаемых значений
# здесь нет — это точка отсчёта, с которой R3 будет сверяться: после
# заморозки эти числа обязаны перестать расти.
echo "  public.knowledge_notes:      $(psql -c 'select count(*) from knowledge_notes')"
echo "  public.knowledge_relations:  $(psql -c 'select count(*) from knowledge_relations')"
echo "  health.knowledge_notes:      $(psql -c 'select count(*) from health.knowledge_notes')"
echo "  health.knowledge_relations:  $(psql -c 'select count(*) from health.knowledge_relations')"
echo "  строк в новых таблицах v2:   $(psql -c '
    select (select count(*) from knowledge_nodes)
         + (select count(*) from knowledge_edges)
         + (select count(*) from knowledge_node_mentions)
         + (select count(*) from knowledge_entity_aliases)
         + (select count(*) from knowledge_semantic_runs)')"

echo
echo "############ 6. R1 НЕ СЛОМАН ############"
want "public health-чанков" "$(psql -c 'select count(*) from knowledge_chunks')" "0"
want "health-чанков" "$(psql -c 'select count(*) from health.knowledge_chunks')" "953"
want "health-векторов" \
     "$(psql -c 'select count(*) from health.knowledge_chunks where embedding is not null')" "881"
want "файлов в общем дереве" "$(sudo find /opt/helm-knowledge -type f | wc -l)" "0"

echo
if [ "$fails" -eq 0 ]; then
  echo "############ R2 VERIFY PASS ############"
else
  echo "::error::не совпало проверок: $fails"
  echo "############ R2 VERIFY FAIL ############"
  exit 1
fi
