#!/bin/bash
# HELM v4.0 RESCUE · R3: приёмка атомизатора v2. Read-only.
#
# Что доказывается на живом сервере:
#   1. миграция окон накатилась, схема совпала с моделями;
#   2. RLS на таблице окон, health-зеркало создано, helm_app его не видит;
#   3. закрытые реестры окна закрыты и в базе;
#   4. semantic-v1 по-прежнему заморожен и не растёт;
#   5. R1 и R2 не сломаны.
#
# Чего здесь НЕТ намеренно: прогона извлечения по реальному корпусу.
# §14.22 запрещает backfill до PASS на R4/R5, а один живой прогон «чтобы
# посмотреть» — это и есть начало backfill'а. Качество извлечения меряет
# R4 бенчмарком, не эта приёмка.
set -uo pipefail

fails=0
psql() { sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tA "$@" < /dev/null; }
want() {
  printf '  %-46s %-12s (ожидается %s)' "$1" "$2" "$3"
  if [ "$2" = "$3" ]; then echo; else echo "   ← НЕ СОВПАЛО"; fails=$((fails + 1)); fi
}

echo "############ 1. РЕВИЗИЯ СХЕМЫ ############"
echo -n "  выкачено: "; sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo "отметки нет"
cd /opt/helm/compose
echo -n "  alembic:  "
sudo docker compose exec -T helm-core python3 -m alembic current 2>/dev/null | tail -1

echo
echo "############ 2. ТАБЛИЦА ОКОН ############"
want "public.knowledge_semantic_windows (RLS/FORCE)" \
     "$(psql -c "select coalesce(
        (select case when relrowsecurity then 't' else 'f' end || '/' ||
                    case when relforcerowsecurity then 't' else 'f' end
           from pg_class where relname = 'knowledge_semantic_windows'
            and relnamespace = 'public'::regnamespace), 'НЕТ ТАБЛИЦЫ')")" "t/t"
want "политика изоляции" \
     "$(psql -c "select count(*) from pg_policies
                  where schemaname = 'public'
                    and tablename = 'knowledge_semantic_windows'
                    and policyname = 'knowledge_tenant_isolation'")" "1"
want "health.knowledge_semantic_windows создана" \
     "$(psql -c "select count(*) from pg_tables
                  where schemaname = 'health' and tablename = 'knowledge_semantic_windows'")" "1"
want "helm_app SELECT на health-зеркало" \
     "$(psql -c "select has_table_privilege('helm_app', 'health.knowledge_semantic_windows', 'SELECT')")" "f"

echo
echo "############ 3. РЕЕСТР СТАТУСОВ ОКНА ЗАКРЫТ ############"
for schema in public health; do
  want "$schema: CHECK на статус и границы" \
       "$(psql -c "select count(*) from pg_constraint c
                     join pg_class t on t.oid = c.conrelid
                     join pg_namespace n on n.oid = t.relnamespace
                    where n.nspname = '$schema' and t.relname = 'knowledge_semantic_windows'
                      and c.contype = 'c'")" "2"
done
# Реальная попытка записи мимо реестра, с откатом. Нарушается ровно
# одно ограничение: всё остальное в строке верно.
bogus=$(psql -c "begin;
  insert into knowledge_semantic_windows
    (id, knowledge_user_id, semantic_run_id, source_id, ordinal,
     char_start, char_end, text_hash, status, nodes_created, edges_created,
     rejected_count, created_at)
  select gen_random_uuid(), r.knowledge_user_id, r.id, r.source_id, 0, 0, 10,
         repeat('0', 64), 'нет', 0, 0, 0, now()
    from knowledge_semantic_runs r limit 1;
  rollback;" 2>&1 | grep -c 'ck_knowledge_semantic_windows_status')
echo "  проверка неизвестного статуса: $bogus (0 = прогонов ещё нет, вставлять не от чего)"

echo
echo "############ 4. SEMANTIC-V1 ВСЁ ЕЩЁ ЗАМОРОЖЕН ############"
want "public.knowledge_notes" "$(psql -c 'select count(*) from knowledge_notes')" "0"
want "public.knowledge_relations" "$(psql -c 'select count(*) from knowledge_relations')" "0"
want "health.knowledge_notes" "$(psql -c 'select count(*) from health.knowledge_notes')" "0"

echo
echo "############ 5. R1 И R2 НЕ СЛОМАНЫ ############"
want "public health-чанков" "$(psql -c 'select count(*) from knowledge_chunks')" "0"
want "health-чанков" "$(psql -c 'select count(*) from health.knowledge_chunks')" "953"
want "health-векторов" \
     "$(psql -c 'select count(*) from health.knowledge_chunks where embedding is not null')" "881"
want "файлов в общем дереве" "$(sudo find /opt/helm-knowledge -type f | wc -l)" "0"
for trg in knowledge_sources_current_semantic_run_guard \
           knowledge_semantic_runs_current_guard; do
  want "триггер $trg" \
       "$(psql -c "select count(*) from pg_trigger where tgname = '$trg' and not tgisinternal")" "1"
done

echo
echo "############ 6. ГРАФ ПОКА ПУСТ (ожидаемо до R5) ############"
# §14.22: «No full 90-document backfill before R4/R5 PASS». Ноль здесь —
# соблюдение плана, а не незавершённость.
for t in knowledge_semantic_runs knowledge_semantic_windows knowledge_nodes knowledge_edges; do
  echo "  $t: $(psql -c "select count(*) from $t")"
done

echo
if [ "$fails" -eq 0 ]; then
  echo "############ R3 VERIFY PASS ############"
else
  echo "::error::не совпало проверок: $fails"
  echo "############ R3 VERIFY FAIL ############"
  exit 1
fi
