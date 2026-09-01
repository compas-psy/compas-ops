#!/bin/bash
# Прошлый скрипт (ORM, docker compose exec python) нашёл "0 health-чанков"
# даже с чисто ASCII-фильтром domain='health' — значит дело не в
# кириллице в heredoc (та гипотеза не подтвердилась второй раз подряд).
# Проверяем самое простое: а какой у "Врачи.zip" вообще domain? Через
# обычный psql (без ORM/docker exec python) — метод, который весь этот
# сеанс работал без сюрпризов.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

echo '=== domain источников батча Врачи.zip ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select s.id, s.domain, s.original_filename, s.status
   from knowledge_sources s
   join knowledge_batch_items i on i.source_id = s.id
   join knowledge_ingest_batches b on b.id = i.batch_id
   where b.archive_filename = 'Врачи.zip'"

echo '=== сколько чанков и сколько с embedding у этих источников ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \
  "select count(*) as chunks, count(*) filter (where c.embedding is not null) as with_embedding
   from knowledge_chunks c
   join knowledge_sources s on s.id = c.source_id
   join knowledge_batch_items i on i.source_id = s.id
   join knowledge_ingest_batches b on b.id = i.batch_id
   where b.archive_filename = 'Врачи.zip'"

echo '=== все domain-значения вообще в knowledge_sources (сколько строк на каждый) ==='
sudo docker exec helm-postgres-1 psql -U helm -d helm -c \
  "select domain, count(*) from knowledge_sources group by domain order by 2 desc"
