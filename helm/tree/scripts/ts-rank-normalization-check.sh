#!/bin/bash
# Владелец: "каких врачей я посещал" должен показывать СПЕЦИАЛЬНОСТЬ в
# первую очередь, но лексика находит короткие "Врач: Имя" (без
# специальности) раньше, чем "Врач уролог: Кириченко..." (со
# специальностью, но чуть длиннее). Гипотеза: ts_rank(normalization=2)
# делит на длину В ЛЕКСЕМАХ, штрафуя более длинные чанки даже при
# небольшой разнице. Проверяем ФАКТ, не гадаем: реальный ранг обоих
# типов чанков при normalization=2 (текущий) и normalization=1
# (логарифмическое затухание, менее агрессивный штраф) — прежде чем
# менять код.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

sudo docker exec helm-postgres-1 psql -U helm -d helm -c "
select
  left(c.text, 60) as текст,
  length(c.text) as длина_символов,
  ts_rank(c.tsv, plainto_tsquery('russian', 'врачей'), 2) as rank_norm2,
  ts_rank(c.tsv, plainto_tsquery('russian', 'врачей'), 1) as rank_norm1,
  ts_rank(c.tsv, plainto_tsquery('russian', 'врачей'), 0) as rank_norm0
from knowledge_chunks c
join knowledge_sources s on s.id = c.source_id
where s.domain = 'health'
  and c.tsv @@ plainto_tsquery('russian', 'врачей')
order by rank_norm2 desc
limit 15
"
