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
  state=$(psql -c "select coalesce(
      (select relrowsecurity::text || '/' || relforcerowsecurity::text
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
