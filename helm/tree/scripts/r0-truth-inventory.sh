#!/bin/bash
# HELM v4.0 RESCUE · R0 «Freeze + truth» (§14.22, §30.8.5 B).
#
# Read-only. Ничего не пишет, не мигрирует, не запускает backfill.
# Задача одна: зафиксировать ФАКТ на живом сервере до первой строчки
# кода rescue — что реально выкачено, какая ревизия схемы, сколько чего
# в корпусе и где физически лежит health.
#
# «Deployed SHA» на сервере нигде не записан (deploy.yml раскладывает
# файлы, но не оставляет отметки о коммите), поэтому вместо доверия к
# HEAD ветки печатается ОТПЕЧАТОК выкаченного кода: sha256 от
# отсортированного списка sha256 всех .py в /opt/helm/control-plane/
# helm_core. Сопоставление отпечатка с коммитом делается на стороне
# агента (scripts/r0-match-deployed-sha.sh) — сервер сам про git ничего
# не знает.
#
# Запускается: workflow «Выкат на сервер» → action=recon,
# script=r0-truth-inventory.sh
set -uo pipefail

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm "$@"; }

echo "############ 1. ОТПЕЧАТОК ВЫКАЧЕННОГО КОДА ############"
echo "--- отметка ревизии (появляется начиная с deploy от 02.09.2026) ---"
sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo "отметки нет — ревизия только по отпечатку ниже"
echo "--- helm_core: файлов .py и общий отпечаток ---"
sudo find /opt/helm/control-plane/helm_core -name '*.py' -type f | wc -l
# LC_ALL=C обязателен: без него порядок задаёт локаль оболочки, а в
# UTF-8-локали пунктуация при сортировке игнорируется — `hooks.py` и
# `hooks_knowledge_telegram.py` меняются местами относительно порядка в
# локали C. Первый прогон 02.09.2026 из-за этого дал отпечаток, не
# совпавший ни с одним коммитом, хотя все 59 файлов побайтово равны HEAD.
(cd /opt/helm/control-plane && sudo find helm_core -name '*.py' -type f -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sudo sha256sum \
  | sha256sum)
echo "--- самый свежий файл helm_core (когда выкатывали) ---"
sudo find /opt/helm/control-plane/helm_core -name '*.py' -type f -printf '%T+ %p\n' \
  | sort | tail -1
echo "--- отдельно atomizer.py: он и есть предмет спора F1/F2 ---"
sudo sha256sum /opt/helm/control-plane/helm_core/knowledge/atomizer.py 2>/dev/null \
  || echo "atomizer.py на сервере НЕТ"
sudo grep -n 'MAX_INPUT_CHARS\s*=\|MAX_ATOMS_PER_CALL\s*=\|format=_RESPONSE_SCHEMA\|format="json"' \
  /opt/helm/control-plane/helm_core/knowledge/atomizer.py 2>/dev/null \
  || echo "(строк не найдено)"

echo
echo "############ 2. РЕВИЗИЯ СХЕМЫ И КОНТЕЙНЕРЫ ############"
cd /opt/helm/compose || exit 1
sudo docker compose exec -T helm-core python3 -m alembic current 2>&1 | tail -5
echo "--- контейнеры ---"
sudo docker compose ps --format '{{.Service}}\t{{.Status}}'

echo
echo "############ 3. КОРПУС: ИСТОЧНИКИ ############"
psql -c "
select domain, status, count(*) as sources, count(distinct knowledge_user_id) as users,
       min(created_at)::date as первый, max(created_at)::date as последний
from knowledge_sources group by domain, status order by domain, status"

echo "--- источники по владельцам ---"
psql -c "select knowledge_user_id, count(*) from knowledge_sources group by 1 order by 2 desc"

echo
echo "############ 4. КОРПУС: ЧАНКИ И ЭМБЕДДИНГИ, public vs health ############"
psql -c "
select 'public.knowledge_chunks' as таблица, count(*) as чанков,
       count(embedding) as с_эмбеддингом, sum(length(text)) as символов
from knowledge_chunks"
psql -c "
select 'health.knowledge_chunks' as таблица, count(*) as чанков,
       count(embedding) as с_эмбеддингом, sum(length(text)) as символов
from health.knowledge_chunks" 2>&1 | tail -5

echo
echo "############ 5. F14: ГДЕ ЛЕЖИТ ТЕКСТ HEALTH-ИСТОЧНИКОВ ############"
psql -c "
select count(*) as health_источников,
       count(*) filter (where exists (select 1 from knowledge_chunks c where c.source_id = s.id)) as текст_в_public,
       count(*) filter (where exists (select 1 from health.knowledge_chunks c where c.source_id = s.id)) as текст_в_health
from knowledge_sources s where s.domain = 'health'"
echo "--- сколько именно чанков health-источников физически в public ---"
psql -c "
select count(*) as чанков_health_в_public, sum(length(c.text)) as символов
from knowledge_chunks c join knowledge_sources s on s.id = c.source_id
where s.domain = 'health'"
echo "--- утечка имени файла в public-конверте (ожидается 0 после P12) ---"
psql -c "
select count(*) filter (where original_filename is not null) as имя_в_public,
       count(*) filter (where original_filename is null) as имя_вынесено
from knowledge_sources where domain = 'health'"
psql -c "select count(*) as строк_в_health_sidecar from health.knowledge_source_private" 2>&1 | tail -4

echo
echo "############ 6. LEGACY SEMANTIC-V1: notes/relations ############"
psql -c "
select 'public.knowledge_notes' as таблица, count(*) as строк,
       count(distinct slug) as уникальных_slug, count(distinct type) as типов
from knowledge_notes"
psql -c "select type, count(*) from knowledge_notes group by 1 order by 2 desc" 2>&1 | tail -15
psql -c "
select 'public.knowledge_relations' as таблица, count(*) as строк,
       count(*) filter (where evidence_type = 'explicit_link') as explicit_link,
       count(distinct relation_type) as типов_связи
from knowledge_relations"
psql -c "select relation_type, evidence_type, count(*) from knowledge_relations group by 1,2 order by 3 desc" 2>&1 | tail -15
psql -c "select 'health.knowledge_notes' as таблица, count(*) from health.knowledge_notes" 2>&1 | tail -4
psql -c "select 'health.knowledge_relations' as таблица, count(*) from health.knowledge_relations" 2>&1 | tail -4

echo
echo "############ 7. MICRO-MEMORY, ПАЧКИ, ОЧЕРЕДЬ ############"
psql -c "select count(*) as memories from knowledge_memories" 2>&1 | tail -4
psql -c "select status, count(*) from knowledge_ingest_jobs group by 1 order by 2 desc" 2>&1 | tail -10
psql -c "select status, count(*) from knowledge_ingest_batches group by 1 order by 2 desc" 2>&1 | tail -10

echo
echo "############ 8. ФАЙЛОВАЯ СИСТЕМА VAULT (F15) ############"
for d in /opt/helm-knowledge /opt/helm-knowledge-private; do
  echo "--- $d ---"
  if sudo test -d "$d"; then
    sudo du -sh "$d" 2>/dev/null
    sudo find "$d" -maxdepth 2 -type d -printf '%p\n' 2>/dev/null | sort | head -40
    echo "    файлов .md всего: $(sudo find "$d" -name '*.md' -type f 2>/dev/null | wc -l)"
    echo "    файлов всего:     $(sudo find "$d" -type f 2>/dev/null | wc -l)"
  else
    echo "каталога нет"
  fi
done
echo "--- .md по подкаталогам общего vault (сюда же легли бы health-заметки) ---"
sudo find /opt/helm-knowledge -name '*.md' -type f -printf '%h\n' 2>/dev/null | sort | uniq -c | sort -rn | head -20

echo
echo "############ 9. БЭКАП И МЕСТО ############"
stat -c '%y' /var/lib/helm-guardian/last-backup 2>/dev/null || echo "отметки о бэкапе нет"
sudo ls -lh /opt/helm/backups 2>/dev/null | tail -5 || echo "каталога бэкапов нет"
df -h / | tail -1
free -h | head -2

echo
echo "############ 10. F13: RepoGraphify vs KnowledgeGraphify ############"
sudo ls -d /opt/helm-knowledge/derived/graphify 2>/dev/null || echo "derived/graphify: НЕТ (KnowledgeGraphify не существует)"
sudo ls /opt/helm/graph 2>/dev/null | head || echo "/opt/helm/graph: нет (RepoGraphify живёт в репозитории, не на сервере)"

echo
echo "############ ГОТОВО ############"
