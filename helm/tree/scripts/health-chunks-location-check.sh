#!/bin/bash
# ADR-019 фаза 4: пробный прогон atomizer-dryrun-health-check.sh нашёл
# chunks=0 для всех 3 старейших health-источников — гипотеза: они
# загружены ДО первого прогона setup-health-role.sh (см. историю коммитов
# "fix(recon): health-чанки пока в public, не в health.* (P12 ещё не
# выкачен)", 01.09 11:46), а migrate-health-filenames-to-sidecar.sh
# переносил только original_filename, не сам текст чанков. Проверяем факт
# напрямую через psql (суперпользователь, видит обе схемы, RLS не мешает).
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

sudo docker exec helm-postgres-1 psql -U helm -d helm -c "
select 'public' as где, count(*) as всего_health_источников,
       count(*) filter (where exists (
         select 1 from knowledge_chunks c where c.source_id = s.id
       )) as с_чанками_в_public
from knowledge_sources s
where s.domain = 'health'
"

sudo docker exec helm-postgres-1 psql -U helm -d helm -c "
select 'health' as где, count(*) as всего_health_источников,
       count(*) filter (where exists (
         select 1 from health.knowledge_chunks c where c.source_id = s.id
       )) as с_чанками_в_health
from knowledge_sources s
where s.domain = 'health'
"
