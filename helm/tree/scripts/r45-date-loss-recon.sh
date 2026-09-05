#!/bin/bash
# HELM · где именно умирают даты (§6 QUERY_LAYER_UNIVERSALITY).
#
# action=recon: только SELECT, ничего не пишет.
#
# Распоряжение владельца 05.09.2026: «Сначала показать конкретно, где и
# почему R4.5 отбрасывает достоверные даты. После этого принимать
# решение об изменении grounding.» Замер сделан ДО правки и без правки.
#
# Замерено раньше: occurred_at_start пуст у всех 388 узлов графа.
# «Гейт R4.5 виноват» — это гипотеза, а подозреваемых четыре, и они
# требуют разных исправлений:
#
#   П1. модель вообще не выставила occurred_at;
#   П2. гейт «относительная дата в цитате» отбросил атом целиком
#       (_RELATIVE_DATE_MARKERS_RE);
#   П3. гейт «нет абсолютной даты в цитате» отбросил атом целиком
#       (_ABSOLUTE_DATE_RE при точности day/month/year);
#   П4. дата дошла до записи, но parse_occurred_at() не разобрал строку
#       (принимает ровно ГГГГ-ММ-ДД / ГГГГ-ММ / ГГГГ) и молча вернул
#       (None, unknown) — БЕЗ отказа и без следа в rejected_count.
#
# Что этот замер различает, а что нет — честно (правка владельца
# 05.09.2026, первая редакция это завышала):
#   П1 и П4 НЕ РАЗДЕЛЯЮТСЯ. После записи в БД «модель не дала дату и
#       поставила unknown» и «модель дала „август 2026“, а
#       parse_occurred_at() не разобрал и вернул (None, unknown)»
#       выглядят одинаково: date_precision='unknown' при пустом
#       occurred_at_start. Сюда же попадает точность вне реестра
#       (semantic_extract.py:535). Блок 1 — это верхняя граница
#       «П1 или П4», список кандидатов, а не измеренный П4;
#   П2+П3 вместе ограничиваются сверху, и граница грубая вдвойне:
#       rejected_count считает ВСЕ причины, не только датные, причины не
#       сохраняются — только их число (semantic_publish.py:374), — а
#       часть записей там вообще не потеря атома (точность вне реестра
#       заносится в rejected, но атом остаётся, semantic_extract.py:535).
#
# Восстанавливать П4 из данных, которых уже нет, не пытаемся. Если после
# полного R8 его понадобится отделить — минимальный диагностический
# повтор на выборке источников либо инструментовка ДО
# parse_occurred_at() с двумя счётчиками: `no_date_from_model` и
# `date_parse_failed`. Разделить П2 и П3 между собой — тем же повтором с
# сохранением причин, и только если блок 2 покажет ненулевые отказы.
#
# Блок 3 — потолок возможного: сколько в корпусе кусков, где абсолютная
# дата вообще есть. Если их мало, виноват не гейт, а тексты.
#
# ИМЕНА ФАЙЛОВ И ЦИТАТЫ ПЕЧАТАЮТСЯ — прямые решения владельца
# 05.09.2026 («имена и файлы печатай прямо в отчёт, это мои данные»,
# «цитаты тоже показывай»). Куски режутся до 200 символов: это
# диагностика, а не выгрузка.
set -uo pipefail
cd /opt/helm/compose || exit 1

# Тот же смысл, что у _ABSOLUTE_DATE_RE в semantic_extract.py:319 —
# четырёхзначный год, ДД.ММ либо русское название месяца. Хвост года у
# ДД.ММ.ГГГГ опущен: на факт совпадения он не влияет.
ABS='\d{4}|\d{1,2}[./]\d{1,2}|январ|феврал|март|апрел|ма[ей]|июн|июл|август|сентябр|октябр|ноябр|декабр'

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. ТОЧНОСТЬ ДАТЫ × НАЛИЧИЕ ДАТЫ ############"
echo "верхняя граница «П1 или П4»: unknown при пустой дате — это кандидаты,"
echo "а не измеренный П4; два случая после записи неразличимы."
echo "формат: точность | узлов | из них с occurred_at_start"
for schema in health public; do
  echo "-- $schema:"
  psql "select coalesce(date_precision,'(null)')||' | '||count(*)::text||' | '||count(occurred_at_start)::text
        from ${schema}.knowledge_nodes group by date_precision order by count(*) desc"
done

echo "############ 2. ОТКАЗЫ ВАЛИДАТОРА — ВЕРХНЯЯ ГРАНИЦА (П2+П3) ############"
echo "ноль отказов означает «П2/П3 не являются причиной текущей потери дат»,"
echo "а не оправдание гейтов вообще: причины отказов не сохраняются."
echo "формат: окон | из них с отказами | отказов всего"
for schema in health public; do
  echo "-- $schema:"
  psql "select count(*)::text||' | '||(count(*) filter (where rejected_count>0))::text||' | '||
               coalesce(sum(rejected_count),0)::text
        from ${schema}.knowledge_semantic_windows"
  echo "   по статусу окна (статус | окон | отказов):"
  psql "select status||' | '||count(*)::text||' | '||coalesce(sum(rejected_count),0)::text
        from ${schema}.knowledge_semantic_windows group by status order by count(*) desc"
done

echo "############ 3. ПОТОЛОК: ЕСТЬ ЛИ ДАТЫ В САМИХ ТЕКСТАХ ############"
echo "формат: кусков | из них с абсолютной датой | источников с такой датой"
for schema in health public; do
  echo "-- $schema:"
  psql "select count(*)::text||' | '||(count(*) filter (where text ~* '${ABS}'))::text||' | '||
               (count(distinct source_id) filter (where text ~* '${ABS}'))::text
        from ${schema}.knowledge_chunks"
done

echo "############ 4. ИСТОЧНИКИ: ДАТЫ В ТЕКСТЕ ПРОТИВ ДАТ В ГРАФЕ ############"
echo "формат: файл | кусков с датой | узлов источника | из них с датой"
psql "with dated as (
        select source_id, count(*) as chunks
        from health.knowledge_chunks where text ~* '${ABS}' group by source_id),
      nodes as (
        select m.source_id, count(distinct n.id) as total,
               count(distinct n.id) filter (where n.occurred_at_start is not null) as with_date
        from health.knowledge_node_mentions m
        join health.knowledge_nodes n on n.id = m.node_id
        group by m.source_id)
      select coalesce(p.original_filename,'(без имени)')||' | '||d.chunks::text||' | '||
             coalesce(nodes.total,0)::text||' | '||coalesce(nodes.with_date,0)::text
      from dated d
      left join nodes on nodes.source_id = d.source_id
      left join health.knowledge_source_private p on p.source_id = d.source_id
      order by d.chunks desc limit 10"

echo "############ 5. ПРИМЕРЫ КУСКОВ С ДАТОЙ ############"
psql "select left(regexp_replace(text, '\s+', ' ', 'g'), 200)
      from health.knowledge_chunks where text ~* '${ABS}' order by length(text) limit 5"

echo "############ ГОТОВО ############"
