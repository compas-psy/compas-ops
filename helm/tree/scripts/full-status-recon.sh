#!/bin/bash
# HELM · сводная разведка живого состояния для FULL-HELM-STATUS.
#
# action=recon: только читает. Ни одного изменения, ни одной записи.
#
# Смысл: статус системы пишется по серверу, а не по документам. Каждая
# строка отчёта — то, что ответила машина.
#
# Наружу — только числа, состояния служб и коды ответов. Ни имён файлов,
# ни подписей сущностей, ни содержимого (§5.2 CLAUDE.md).
set -uo pipefail
cd /opt/helm/compose || exit 1

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc "$1" 2>&1; }

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ КОНТЕЙНЕРЫ ############"
sudo docker compose ps --format '{{.Service}}\t{{.State}}\t{{.Status}}'

echo "############ СХЕМА ############"
sudo docker compose exec -T helm-core python3 -m alembic current 2>&1 | tail -2
echo "роли (ожидается helm_app | f | f):"
psql "select rolname||' | '||rolsuper||' | '||rolbypassrls from pg_roles where rolname in ('helm','helm_app') order by rolname"

echo "############ КОРПУС ############"
echo "источники по домену/статусу:"
psql "select domain||' | '||status||' | '||count(*)::text from knowledge_sources group by domain, status order by domain, status"
echo "чанки public | health:"
psql "select (select count(*) from public.knowledge_chunks)||' | '||(select count(*) from health.knowledge_chunks)"
echo "векторы public | health:"
psql "select (select count(*) from public.knowledge_chunks where embedding is not null)||' | '||(select count(*) from health.knowledge_chunks where embedding is not null)"
echo "micro-memory:"
psql "select count(*) from knowledge_memories"
echo "semantic-v1 (notes | relations), должно быть 0:"
psql "select (select count(*) from public.knowledge_notes)||' | '||(select count(*) from public.knowledge_relations)"

echo "############ SEMANTIC-V2 ############"
echo "ревизии разбора по статусу:"
psql "select status||' | '||count(*)::text from knowledge_semantic_runs group by status order by status"
echo "источники с текущей ревизией:"
psql "select count(*) from knowledge_sources where current_semantic_run_id is not null"
echo "узлы | рёбра | упоминания (public):"
psql "select (select count(*) from public.knowledge_nodes)||' | '||(select count(*) from public.knowledge_edges)||' | '||(select count(*) from public.knowledge_node_mentions)"
echo "узлы | рёбра | упоминания (health):"
psql "select (select count(*) from health.knowledge_nodes)||' | '||(select count(*) from health.knowledge_edges)||' | '||(select count(*) from health.knowledge_node_mentions)"
echo "упоминания с точным спаном (health):"
psql "select count(*) from health.knowledge_node_mentions where char_start is not null"
echo "личности | состав | кандидаты (health):"
psql "select (select count(*) from health.knowledge_entity_identities)||' | '||(select count(*) from health.knowledge_entity_identity_members)||' | '||(select count(*) from health.knowledge_entity_resolution_candidates)"

echo "############ ИЗОЛЯЦИЯ HEALTH ############"
echo "helm_app SELECT на health (ожидается f везде):"
psql "select t||' | '||has_table_privilege('helm_app','health.'||t,'select') from unnest(array['knowledge_chunks','knowledge_nodes','knowledge_edges','knowledge_node_mentions','knowledge_entity_identities']) t"
echo "файлов в ОБЩЕМ дереве знаний (ожидается 0):"
sudo find /opt/helm-knowledge -type f 2>/dev/null | wc -l
echo "файлов в приватном дереве:"
sudo find /opt/helm-knowledge-private -type f 2>/dev/null | wc -l

echo "############ СТРАХОВКИ ############"
echo "последний бэкап:"
sudo stat -c %y /var/lib/helm-guardian/last-backup 2>/dev/null || echo "отметки нет"
echo "последний тест восстановления:"
sudo stat -c %y /var/lib/helm-guardian/last-restore-test 2>/dev/null || echo "отметки нет"
echo "локальные точки возврата:"
sudo ls -1 /opt/helm/checkpoints 2>/dev/null | wc -l

echo "############ СЛУЖБЫ ############"
echo "guardian (юнит может называться иначе — печатаем все подходящие):"
sudo systemctl list-units --type=service --all --no-legend 2>/dev/null \
  | grep -i guardian || echo "юнита с таким именем нет"
echo "публичный статус guardian:"
sudo cat /var/lib/helm-guardian/public-status.json 2>/dev/null | head -1 || echo "нет файла"
probe() {
  local code
  code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' --noproxy '*' "$2" 2>/dev/null || echo "нет ответа")
  echo "$1: $code"
}
probe helm-core http://127.0.0.1:8080/internal/status
probe litellm   http://127.0.0.1:4000/health/liveliness
probe n8n       http://127.0.0.1:5678/healthz
probe ollama    http://127.0.0.1:11434/api/version
probe embed     http://127.0.0.1:8000/health
echo "forgejo снаружи:"
curl -s -o /dev/null -m 8 -w '%{http_code}\n' https://git.cmpas.ru/ 2>/dev/null || echo "нет ответа"
echo "панель снаружи:"
curl -s -o /dev/null -m 8 -w '%{http_code}\n' https://helm.cmpas.ru/ 2>/dev/null || echo "нет ответа"

echo "############ РЕСУРСЫ ############"
df -h / | tail -1
free -h | head -2
echo "swap in use:"
free -m | awk '/Swap/ {print $3" MB из "$2" MB"}'

echo "############ ГОТОВО ############"
exit 0
