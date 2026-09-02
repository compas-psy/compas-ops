#!/bin/bash
# HELM v4.0 RESCUE · R4: приёмка бенчмарка локального semantic extractor.
# Read-only.
#
# Что доказывается на живом сервере после живого прогона
# r4-golden-benchmark.sh:
#   1. граф semantic-v2 по-прежнему пуст — бенчмарк не писал через
#      publish_semantic_run() (владелец п.1, п.10: «0 semantic graph
#      backfill», «0 current_semantic_run_id changes»);
#   2. извлекатель структурно не может звать LiteLLM/OpenRouter —
#      проверка идёт по РАЗВЁРНУТОМУ файлу на сервере, не по репозиторию
#      в песочнице (п.10: «0 LiteLLM calls», «0 OpenRouter calls»);
#   3. semantic-v1 по-прежнему заморожен (R1/R2/R3 regressions green);
#   4. результаты бенчмарка реально лежат на диске и харнесс
#      воспроизводим (п.10: «benchmark reproducible from committed
#      harness»);
#   5. живые сервисы здоровы.
#
# Чего здесь НЕТ: повторного запуска самого бенчмарка (это отдельный
# recon r4-golden-benchmark.sh) и выбора winner (это разбирается по уже
# полученным JSON, не живым прогоном — сравнение кандидатов не должно
# зависеть от того, что сервер отвечает чуть иначе во второй раз).
set -uo pipefail

fails=0
psql() { sudo docker exec -i helm-postgres-1 psql -U helm -d helm -tA "$@" < /dev/null; }
want() {
  printf '  %-52s %-12s (ожидается %s)' "$1" "$2" "$3"
  if [ "$2" = "$3" ]; then echo; else echo "   ← НЕ СОВПАЛО"; fails=$((fails + 1)); fi
}

echo "############ 1. РЕВИЗИЯ СХЕМЫ ############"
echo -n "  выкачено: "; sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo "отметки нет"
cd /opt/helm/compose
echo -n "  alembic:  "
sudo docker compose exec -T helm-core python3 -m alembic current 2>/dev/null | tail -1

echo
echo "############ 2. ГРАФ ПО-ПРЕЖНЕМУ ПУСТ (бенчмарк не публиковал) ############"
for t in knowledge_semantic_runs knowledge_semantic_windows knowledge_nodes knowledge_edges \
         knowledge_entity_aliases knowledge_node_mentions; do
  want "public.$t" "$(psql -c "select count(*) from $t")" "0"
done
want "health.knowledge_nodes" "$(psql -c 'select count(*) from health.knowledge_nodes')" "0"
want "health.knowledge_edges" "$(psql -c 'select count(*) from health.knowledge_edges')" "0"
want "current_semantic_run_id везде NULL" \
     "$(psql -c 'select count(*) from knowledge_sources where current_semantic_run_id is not null')" "0"

echo
echo "############ 3. ИЗВЛЕКАТЕЛЬ СТРУКТУРНО НЕ ЗНАЕТ LITELLM/OPENROUTER ############"
# Проверка по РАЗВЁРНУТОМУ файлу внутри контейнера, не по локальному
# репозиторию — иначе доказывается «в песочнице так», а не «на сервере
# так». Единственный внешний адрес — OLLAMA_URL, тот же принцип, что и
# в юнит-тесте test_extraction_never_leaves_the_machine.
bad_refs=$(sudo docker compose exec -T helm-core python3 -c "
import ast, inspect
import helm_core.knowledge.semantic_extract as module
tree = ast.parse(inspect.getsource(module))
urls = [n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and '://' in n.value]
print(len([u for u in urls if u != module.OLLAMA_URL]))
")
want "посторонние URL в semantic_extract.py на сервере" "$bad_refs" "0"
ollama_only=$(sudo docker compose exec -T helm-core python3 -c "
import helm_core.knowledge.semantic_extract as module
print('OK' if module.OLLAMA_URL.startswith('http://ollama:') else 'BAD')
")
want "OLLAMA_URL указывает на локальный ollama" "$ollama_only" "OK"

echo
echo "############ 4. РЕЗУЛЬТАТЫ БЕНЧМАРКА НА ДИСКЕ (§14.18: не self-generated gold) ############"
RUN_DIR=/opt/helm-state/benchmarks/r4/run1
for f in golden-gemma2_2b.json golden-qwen2_5_3b.json; do
  size=$(sudo stat -c '%s' "$RUN_DIR/$f" 2>/dev/null || echo 0)
  want "$f непустой" "$([ "$size" -gt 0 ] && echo да || echo нет)" "да"
done
echo "  (qwen2.5:7b — опционален, зависит от resource preflight на момент прогона)"
if [ -s "$RUN_DIR/golden-qwen2_5_7b.json" ]; then
  echo "  qwen2.5:7b: результат есть"
else
  echo "  qwen2.5:7b: результата нет (preflight не пройден или ещё не прогнан)"
fi

echo
echo "############ 5. HARNESS ВОСПРОИЗВОДИМ ИЗ ЗАКОММИЧЕННОГО КОДА ############"
# Не «доверять записанному отчёту», а прогнать харнесс СЕЙЧАС на одном
# дешёвом golden-кейсе через ту же самую команду, которой пользовался
# recon — если модуль сломан/не тот, что в отчёте, здесь будет видно.
smoke=$(sudo docker compose exec -T helm-core python3 -c "
from helm_core.knowledge.semantic_benchmark import run_golden_benchmark
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
cases = tuple(c for c in GOLDEN_CASES if c.case_id == 'no_knowledge')
def fake(text, *, domain, heading_path=(), model, keep_alive=None):
    from helm_core.knowledge.semantic_extract import WindowExtraction
    return WindowExtraction()
report = run_golden_benchmark(model='smoke', extract_fn=fake, stability_repeats=1, cases=cases)
print('OK' if report.schema_stats.cases_total == 1 and report.metrics.no_knowledge_violations == 0 else 'BAD')
")
want "харнесс запускается на живом сервере" "$smoke" "OK"

echo
echo "############ 6. R1/R2/R3 НЕ СЛОМАНЫ ############"
want "public health-чанков" "$(psql -c 'select count(*) from knowledge_chunks')" "0"
want "health-чанков" "$(psql -c 'select count(*) from health.knowledge_chunks')" "953"
want "public.knowledge_notes" "$(psql -c 'select count(*) from knowledge_notes')" "0"
want "health.knowledge_notes" "$(psql -c 'select count(*) from health.knowledge_notes')" "0"
for trg in knowledge_sources_current_semantic_run_guard \
           knowledge_semantic_runs_current_guard; do
  want "триггер $trg" \
       "$(psql -c "select count(*) from pg_trigger where tgname = '$trg' and not tgisinternal")" "1"
done
for schema_name in public health; do
  want "$schema_name.knowledge_nodes.statement_text существует" \
       "$(psql -c "select count(*) from information_schema.columns
                     where table_schema = '$schema_name' and table_name = 'knowledge_nodes'
                       and column_name = 'statement_text'")" "1"
done

echo
echo "############ 7. ЖИВЫЕ СЕРВИСЫ ЗДОРОВЫ ############"
sudo docker compose ps --format "{{.Service}}: {{.Status}}"
unhealthy=$(sudo docker compose ps --format "{{.Service}} {{.Status}}" \
            | grep -Ev "healthy|Up [0-9]" | wc -l)
want "сервисов без явного здорового статуса" "$unhealthy" "0"

echo
if [ "$fails" -eq 0 ]; then
  echo "############ R4 VERIFY PASS ############"
else
  echo "::error::не совпало проверок: $fails"
  echo "############ R4 VERIFY FAIL ############"
  exit 1
fi
