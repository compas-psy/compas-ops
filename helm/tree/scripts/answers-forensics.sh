#!/bin/bash
# HELM · разбор реальных production-ответов Второго мозга.
#
# action=recon: только читает. Ни одной записи в БД.
#
# Распоряжение владельца 05.09.2026: «Возьми последние 10 моих реальных
# запросов во Второй мозг вместе с фактическими ответами». Этот скрипт
# отвечает на предварительный вопрос: что из этого вообще сохранено.
#
# Наружу (в журнал GitHub Actions) — только числа, коды режимов, длины и
# структурные признаки. Ни одного символа вопроса или ответа: это
# медицинские данные владельца, а журнал Actions хранится у GitHub
# бессрочно (§5.2 CLAUDE.md). Полный текст — в файл 0600 на сервере.
set -uo pipefail
cd /opt/helm/compose || exit 1

OUT=/opt/helm-knowledge-private/forensics
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. ЖУРНАЛ ОТВЕТОВ (knowledge_answer_runs) ############"
echo "всего строк:"
psql "select count(*) from knowledge_answer_runs"
echo "по дням (день | режим | платно | строк):"
psql "select date_trunc('day', created_at)::date||' | '||mode||' | '||paid_ai_used::text||' | '||count(*)::text
      from knowledge_answer_runs group by 1,2,3 order by 1 desc, 2 limit 40"
echo "последние 15 (время | режим | evidence | платно | домен | latency | локальная модель | причина эскалации):"
psql "select to_char(created_at,'YYYY-MM-DD HH24:MI')||' | '||mode||' | '||evidence_count::text||' | '
      ||paid_ai_used::text||' | '||coalesce(domain,'-')||' | '||coalesce(latency_ms::text,'-')||' | '
      ||coalesce(local_model,'-')||' | '||coalesce(escalation_reason,'-')
      from knowledge_answer_runs order by created_at desc limit 15"

echo "############ 2. ЕСТЬ ЛИ ГДЕ-ТО ТЕКСТ ВОПРОСА ############"
echo "tasks всего | с непустым title_redacted:"
psql "select count(*)||' | '||count(title_redacted) from tasks"
echo "tasks по каналу (канал | строк | последняя):"
psql "select origin_channel||' | '||count(*)::text||' | '||coalesce(to_char(max(created_at),'YYYY-MM-DD HH24:MI'),'-')
      from tasks group by 1 order by 1"
echo "channel_events по каналу (канал | строк | последнее):"
psql "select channel||' | '||count(*)::text||' | '||to_char(max(received_at),'YYYY-MM-DD HH24:MI')
      from channel_events group by 1 order by 1"
echo "task_events с непустым payload_redacted (тип | строк):"
psql "select event_type||' | '||count(*)::text from task_events where payload_redacted is not null
      group by 1 order by 2 desc limit 15"

echo "############ 3. ТЕКСТ ОТВЕТА (outbox) ############"
echo "по каналу и статусу (канал | статус | строк | с текстом):"
psql "select channel||' | '||status||' | '||count(*)::text||' | '||count(payload_reference->>'text')::text
      from outbox group by 1,2 order by 1,2"
echo "последние 20 ответов, структура (время | канал | символов | строк | «Найдено N совпадений» | «Источник:» | нумерованных пунктов):"
psql "select to_char(next_attempt_at,'YYYY-MM-DD HH24:MI')||' | '||channel||' | '
      ||length(payload_reference->>'text')::text||' | '
      ||(1 + (length(payload_reference->>'text') - length(replace(payload_reference->>'text', chr(10), ''))))::text||' | '
      ||(payload_reference->>'text' ~ 'Найдено [0-9]+ совпадений')::text||' | '
      ||(payload_reference->>'text' like '%Источник:%')::text||' | '
      ||(select count(*) from regexp_matches(payload_reference->>'text', '(?n)^[0-9]+\. ', 'g'))::text
      from outbox where payload_reference->>'text' is not null
      order by next_attempt_at desc limit 20"

echo "############ 4. ХРАНИЛИЩЕ СЕССИЙ HERMES ############"
HD=/home/helm/.hermes
if sudo test -d "$HD"; then
  echo "каталоги первого уровня (имя | файлов | суммарный размер):"
  for d in $(sudo find "$HD" -maxdepth 1 -mindepth 1 -type d -printf '%f\n' 2>/dev/null | sort); do
    n=$(sudo find "$HD/$d" -type f 2>/dev/null | wc -l)
    s=$(sudo du -sh "$HD/$d" 2>/dev/null | cut -f1)
    echo "  $d | $n | $s"
  done
  echo "файлы с расширением jsonl/json/db, изменённые за 14 дней (расширение | штук):"
  sudo find "$HD" -type f \( -name '*.jsonl' -o -name '*.json' -o -name '*.db' -o -name '*.sqlite*' \) \
       -mtime -14 -printf '%f\n' 2>/dev/null | sed 's/.*\.//' | sort | uniq -c | sed 's/^/  /'
  echo "самые свежие 10 файлов (мтайм | размер | путь относительно .hermes):"
  sudo find "$HD" -type f -printf '%TY-%Tm-%Td %TH:%TM|%s|%P\n' 2>/dev/null \
       | sort -r | head -10 | sed 's/^/  /'
else
  echo "  каталога $HD нет"
fi

echo "############ 5. ЖУРНАЛЫ КОНТЕЙНЕРОВ: ЕСТЬ ЛИ ТАМ ТЕКСТ ############"
echo "helm-core: строк за 7 дней | из них с 'knowledge/probe':"
a=$(sudo docker compose logs --since 168h helm-core 2>/dev/null | wc -l)
b=$(sudo docker compose logs --since 168h helm-core 2>/dev/null | grep -c 'knowledge/probe')
echo "  $a | $b"
echo "hermes systemd за 7 дней: строк | из них '[helm-control]':"
c=$(sudo journalctl -u hermes --since '7 days ago' --no-pager 2>/dev/null | wc -l)
d=$(sudo journalctl -u hermes --since '7 days ago' --no-pager 2>/dev/null | grep -c 'helm-control')
echo "  $c | $d"

echo "############ 6. ВЫГРУЗКА ПОЛНОГО ТЕКСТА В ПРИВАТНЫЙ ФАЙЛ ############"
sudo install -d -m 0700 -o helm -g helm "$OUT"
F="$OUT/answers-$STAMP.txt"
TMP=$(mktemp)
psql "select '=== '||to_char(next_attempt_at,'YYYY-MM-DD HH24:MI')||' | '||channel||' | '||status
      ||chr(10)||(payload_reference->>'text')||chr(10)
      from outbox where payload_reference->>'text' is not null
      order by next_attempt_at desc limit 30" > "$TMP"
sudo install -m 0600 -o helm -g helm "$TMP" "$F"
shred -u "$TMP" 2>/dev/null || rm -f "$TMP"
echo "файл: $F"
echo "права | владелец | размер:"
sudo stat -c '%a | %U:%G | %s' "$F"
echo "строк в файле: $(sudo cat "$F" | wc -l)"
echo "############ ГОТОВО ############"
