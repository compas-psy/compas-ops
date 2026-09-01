#!/bin/bash
# Сырой текст чанков из батча "Врачи.zip" — без regex, без кодировочных
# сюрпризов. Цель: увидеть глазами, что реально попало в текст (названия
# специальностей или нет), прежде чем гадать про embeddings/пороги.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

sudo docker exec helm-postgres-1 psql -U helm -d helm -x -c \
  "select c.text
   from knowledge_chunks c
   join knowledge_sources s on s.id = c.source_id
   join knowledge_batch_items i on i.source_id = s.id
   join knowledge_ingest_batches b on b.id = i.batch_id
   where b.archive_filename = 'Врачи.zip'
   order by c.source_id, c.ordinal
   limit 20"
