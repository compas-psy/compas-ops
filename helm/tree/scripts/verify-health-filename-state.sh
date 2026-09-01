#!/bin/bash
# migrate-health-filenames-to-sidecar.sh только что отчитался "найдено 0"
# — неожиданно, раньше (vrachi-domain-check.sh, до этого деплоя) видели
# original_filename прямо в public.knowledge_sources для health-строк.
# Проверяем текущее фактическое состояние напрямую через psql (без ORM),
# чтобы понять: имена уже перенесены в сайдкар кем-то другим, или правда
# отсутствуют вообще.
set -uo pipefail

echo '=== public.knowledge_sources: сколько health-строк ещё с original_filename ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select count(*) from knowledge_sources where domain = 'health' and original_filename is not null"

echo '=== health.knowledge_source_private: сколько строк всего ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select count(*) from health.knowledge_source_private"

echo '=== примеры из health.knowledge_source_private ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select source_id, original_filename from health.knowledge_source_private limit 5"

echo '=== всего health-источников в public.knowledge_sources ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select count(*) from knowledge_sources where domain = 'health'"
