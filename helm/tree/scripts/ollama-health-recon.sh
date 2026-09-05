#!/bin/bash
# HELM · почему извлекатель отвечает «timed out» на трёх последних
# источниках R8.
#
# action=recon: только чтение. Ни одного /api/generate — вызов загрузил
# бы модель в память и изменил постоянное состояние Ollama, а R4.4b
# прямо это запрещает диагностике. Спрашиваются только read-эндпоинты.
#
# Факт, который надо объяснить (прогон 310): три источника обработаны,
# ready=0, degraded=2, failed=1, у каждого ровно одно окно с ошибкой
# «извлекатель недоступен: timed out». Источник в 2986 символов с ОДНИМ
# окном потратил 568 секунд и всё равно провалился.
#
# Почему это не про размер текста: REQUEST_TIMEOUT=120 секунд на вызов,
# при таймауте окно делится детерминированно до MAX_SPLIT_DEPTH=3
# (P2, владелец 04.09.2026). После трёх делений кусок мал настолько, что
# разбирается за секунды. Таймаут на КАЖДОМ уровне означает, что не
# отвечает сам сервис, а не что текст патологический.
#
# Гипотезы, которые различает этот замер:
#   Г1. Ollama не поднят или перезапускается;
#   Г2. поднят, но модель не загружена и не грузится (диск/память);
#   Г3. поднят и отвечает, но машина в свопе — тогда виноват не сервис,
#       а конкуренция за память с embed-сервисом и Postgres;
#   Г4. сервис здоров, и тогда виноват текст этих трёх источников —
#       смотрим их окна.
set -uo pipefail
cd /opt/helm/compose || exit 1

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. КОНТЕЙНЕР OLLAMA (Г1) ############"
sudo docker compose ps ollama 2>&1 | tail -3
echo "перезапусков | статус | старт:"
sudo docker inspect -f '{{.RestartCount}} | {{.State.Status}} | {{.State.StartedAt}}' \
    "$(sudo docker compose ps -q ollama)" 2>&1

echo "############ 2. ОТВЕЧАЕТ ЛИ (Г1/Г2) ############"
echo "версия (таймаут 10с):"
time sudo docker compose exec -T ollama curl -sS -m 10 http://localhost:11434/api/version 2>&1 | tail -2
echo "модели в памяти сейчас (/api/ps):"
sudo docker compose exec -T ollama curl -sS -m 10 http://localhost:11434/api/ps 2>&1 | tail -3
echo "модели на диске (/api/tags, только имена):"
sudo docker compose exec -T ollama curl -sS -m 10 http://localhost:11434/api/tags 2>&1 \
  | tr ',' '\n' | grep -o '"name":"[^"]*"' | head -10

echo "############ 3. ПАМЯТЬ МАШИНЫ (Г3) ############"
free -h 2>&1 | head -3
echo "своп занят | всего:"
free -m 2>&1 | awk '/Swap/ {print $3" МБ | "$2" МБ"}'
echo "топ-5 контейнеров по памяти:"
sudo docker stats --no-stream --format '{{.Name}} | {{.MemUsage}} | {{.CPUPerc}}' 2>&1 \
  | sort -t'|' -k2 -h -r | head -5

echo "############ 4. ЛОГ OLLAMA, ХВОСТ ############"
sudo docker compose logs --tail 25 --no-log-prefix ollama 2>&1 | tail -25

echo "############ 5. ТРИ ЗАСТРЯВШИХ ИСТОЧНИКА (Г4) ############"
echo "источник | окон | из них упало | покрытие | статус ревизии | парсер:"
psql "select left(r.source_id::text,8)||' | '||r.windows_total::text||' | '||
             r.windows_failed::text||' | '||coalesce(r.coverage_ratio,0)::text||' | '||
             r.status||' | '||coalesce(s.parser,'-')
      from knowledge_semantic_runs r
      join knowledge_sources s on s.id = r.source_id
      where s.current_semantic_run_id is distinct from r.id
        and r.created_at > now() - interval '2 hours'
      order by r.created_at desc limit 10"
echo "размеры окон этих ревизий (источник | окно | символов | статус):"
psql "select left(w.source_id::text,8)||' | '||w.ordinal::text||' | '||
             (w.char_end - w.char_start)::text||' | '||w.status
      from health.knowledge_semantic_windows w
      where w.created_at > now() - interval '2 hours'
      order by w.source_id, w.ordinal limit 20"

echo "############ ГОТОВО ############"
