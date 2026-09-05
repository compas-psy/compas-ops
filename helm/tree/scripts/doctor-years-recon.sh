#!/bin/bash
# HELM · что корпус вообще может доказать про ГОД посещения врача.
#
# action=recon: только читает.
#
# Нужно для трёх live-canary (распоряжение владельца 05.09.2026):
# canary 1 спрашивает «в этом году», canary 3 — «в 2014 году», и прежде
# чем спрашивать, надо знать, что корпус вообще способен ответить.
# Иначе «не нашёл за 2014» ничего не докажет: непонятно, сработал ли
# фильтр по году или в корпусе просто нет ни одного датированного
# посещения.
#
# Наружу — только числа и годы. Ни имён, ни подписей, ни цитат.
set -uo pipefail
cd /opt/helm/compose || exit 1

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. РЁБРА ВРАЧЕБНОЙ РОЛИ ############"
echo "public: INVOLVES | из них role=doctor:"
psql "select count(*)||' | '||count(*) filter (where role='doctor')
      from public.knowledge_edges where relation_type='INVOLVES'"
echo "health: INVOLVES | из них role=doctor:"
psql "select count(*)||' | '||count(*) filter (where role='doctor')
      from health.knowledge_edges where relation_type='INVOLVES'"
echo "health: рёбра по типу связи (тип | штук):"
psql "select relation_type||' | '||count(*)::text from health.knowledge_edges
      group by relation_type order by relation_type"

echo "############ 2. ДАТЫ СОБЫТИЙ ############"
echo "health: узлов EVENT всего | с occurred_at_start:"
psql "select count(*)||' | '||count(occurred_at_start)
      from health.knowledge_nodes where kind='EVENT'"
echo "health: год события | узлов (только у кого дата есть):"
psql "select extract(year from occurred_at_start)::int::text||' | '||count(*)::text
      from health.knowledge_nodes
      where kind='EVENT' and occurred_at_start is not null
      group by extract(year from occurred_at_start)
      order by extract(year from occurred_at_start)"
echo "health: год события у СОБЫТИЙ, из которых есть ребро role=doctor (год | рёбер):"
psql "select extract(year from n.occurred_at_start)::int::text||' | '||count(*)::text
      from health.knowledge_edges e
      join health.knowledge_nodes n on n.id = e.from_node_id
      where e.relation_type='INVOLVES' and e.role='doctor'
        and n.occurred_at_start is not null
      group by extract(year from n.occurred_at_start)
      order by extract(year from n.occurred_at_start)"
echo "health: рёбер role=doctor БЕЗ даты события:"
psql "select count(*) from health.knowledge_edges e
      join health.knowledge_nodes n on n.id = e.from_node_id
      where e.relation_type='INVOLVES' and e.role='doctor'
        and n.occurred_at_start is null"

echo "############ 3. ЕСТЬ ЛИ ВООБЩЕ ЧТО-ТО ЗА 2014 ############"
echo "источники с 2014 в имени файла:"
psql "select count(*) from knowledge_sources where original_filename like '%2014%'"
echo "health-узлы с датой в 2014 (любой вид):"
psql "select count(*) from health.knowledge_nodes
      where occurred_at_start >= '2014-01-01' and occurred_at_start < '2015-01-01'"
echo "health-чанки, где встречается «2014»:"
psql "select count(*) from health.knowledge_chunks where text like '%2014%'"
echo "самая ранняя | самая поздняя дата события в health:"
psql "select coalesce(min(occurred_at_start)::date::text,'-')||' | '
      ||coalesce(max(occurred_at_start)::date::text,'-')
      from health.knowledge_nodes where occurred_at_start is not null"

echo "############ 4. ЛИЧНОСТИ ############"
echo "health: личности | из них PERSON | состав:"
psql "select (select count(*) from health.knowledge_entity_identities)||' | '
      ||(select count(*) from health.knowledge_entity_identities where entity_type ilike 'person')||' | '
      ||(select count(*) from health.knowledge_entity_identity_members)"
echo "############ ГОТОВО ############"
