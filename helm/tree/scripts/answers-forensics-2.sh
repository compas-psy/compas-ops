#!/bin/bash
# HELM · разбор production-ответов, волна 2.
#
# Волна 1 (прогон 292) ответила на главный вопрос: текста вопросов в БД
# нет вовсе (tasks: 43 строки, title_redacted непустой — 0), а текст
# ответа лежит в outbox только для MAX и Knowledge-бота. Ответы владельцу
# в Telegram уходят напрямую через шлюз Hermes (`_send_reply`), минуя
# outbox: в БД HELM их нет ни одного.
#
# Осталось одно место, где реальный диалог может сохраниться, — своё
# хранилище Hermes: /home/helm/.hermes/{sessions,logs,kanban.db}. Волна 2
# выясняет, что там есть, НЕ печатая ни одного символа переписки: только
# имена файлов, размеры, число сообщений по ролям и границы по времени.
#
# Плюс исправлены пять SQL-ошибок волны 1 (агрегат внутри сцепленного
# GROUP BY и ORDER BY по позиции вне списка выборки).
set -uo pipefail
cd /opt/helm/compose || exit 1

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "############ 1. ЖУРНАЛ ОТВЕТОВ ПО ДНЯМ ############"
echo "день | режим | платно | строк:"
psql "select date_trunc('day', created_at)::date::text||' | '||mode||' | '||paid_ai_used::text||' | '||count(*)::text
      from knowledge_answer_runs
      group by date_trunc('day', created_at), mode, paid_ai_used
      order by date_trunc('day', created_at) desc, mode"
echo "режим | строк | средний evidence | различных вопросов (по хэшу):"
psql "select mode||' | '||count(*)::text||' | '||round(avg(evidence_count),1)::text||' | '||count(distinct query_hash)::text
      from knowledge_answer_runs group by mode order by mode"

echo "############ 2. ЗАДАЧИ И СОБЫТИЯ КАНАЛОВ ############"
echo "канал | задач | последняя:"
psql "select origin_channel||' | '||count(*)::text||' | '||coalesce(to_char(max(created_at),'YYYY-MM-DD HH24:MI'),'-')
      from tasks group by origin_channel order by origin_channel"
echo "канал | событий | последнее:"
psql "select channel||' | '||count(*)::text||' | '||to_char(max(received_at),'YYYY-MM-DD HH24:MI')
      from channel_events group by channel order by channel"
echo "task_events: тип | строк | из них с непустым payload:"
psql "select event_type||' | '||count(*)::text||' | '||count(payload_redacted)::text
      from task_events group by event_type order by event_type"

echo "############ 3. OUTBOX ############"
echo "канал | статус | строк | с текстом:"
psql "select channel||' | '||status||' | '||count(*)::text||' | '||count(payload_reference->>'text')::text
      from outbox group by channel, status order by channel, status"

echo "############ 4. ХРАНИЛИЩЕ HERMES: ЧТО ТАМ ЕСТЬ ############"
HD=/home/helm/.hermes
echo "-- sessions/ (файл | размер | мтайм) --"
sudo find "$HD/sessions" -type f -printf '  %f | %s | %TY-%Tm-%Td %TH:%TM\n' 2>/dev/null | sort
echo "-- сообщения по ролям в sessions/ (роль | штук) --"
for role in user assistant system tool; do
  n=$(sudo grep -oh "\"role\"[[:space:]]*:[[:space:]]*\"$role\"" -r "$HD/sessions" 2>/dev/null | wc -l)
  echo "  $role | $n"
done
echo "-- границы по времени в sessions/ (ISO-подобные метки: первая | последняя | всего) --"
sudo grep -ohE '20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]' -r "$HD/sessions" 2>/dev/null \
  | sort | awk 'NR==1{f=$0} {l=$0; n++} END{if(n)printf "  %s | %s | %d\n", f, l, n; else print "  метки не найдены"}'
echo "-- kanban.db (размер | таблицы, если есть sqlite3) --"
sudo stat -c '  размер: %s' "$HD/kanban.db" 2>/dev/null || echo "  kanban.db нет"
if command -v sqlite3 >/dev/null 2>&1; then
  echo "  таблицы: $(sudo sqlite3 "$HD/kanban.db" '.tables' 2>&1 | tr '\n' ' ')"
else
  echo "  sqlite3 не установлен — таблицы не смотрел"
fi
echo "-- logs/ (файл | размер | мтайм) --"
sudo find "$HD/logs" -type f -printf '  %f | %s | %TY-%Tm-%Td %TH:%TM\n' 2>/dev/null | sort
echo "-- gateway.log: строк всего | со словом 'telegram' | с 'pre_gateway_dispatch' --"
a=$(sudo cat "$HD/logs/gateway.log" 2>/dev/null | wc -l)
b=$(sudo grep -c telegram "$HD/logs/gateway.log" 2>/dev/null || echo 0)
c=$(sudo grep -c pre_gateway_dispatch "$HD/logs/gateway.log" 2>/dev/null || echo 0)
echo "  $a | $b | $c"
echo "-- agent.log: строк всего | с 'helm-control' | с 'knowledge_probe' --"
d=$(sudo cat "$HD/logs/agent.log" 2>/dev/null | wc -l)
e=$(sudo grep -c 'helm-control' "$HD/logs/agent.log" 2>/dev/null || echo 0)
g=$(sudo grep -c 'knowledge_probe' "$HD/logs/agent.log" 2>/dev/null || echo 0)
echo "  $d | $e | $g"
echo "-- memories/ (файл | размер) --"
sudo find "$HD/memories" -type f -printf '  %f | %s\n' 2>/dev/null | sort

echo "############ 5. ГДЕ ЖИВЁТ HERMES (служба) ############"
sudo systemctl list-units --type=service --all --no-pager --no-legend 2>/dev/null \
  | awk '{print $1}' | grep -i -E 'hermes|gateway' | sed 's/^/  /' || echo "  подходящих юнитов нет"
echo "############ ГОТОВО ############"
