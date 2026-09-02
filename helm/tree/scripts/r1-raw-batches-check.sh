#!/bin/bash
# HELM v4.0 RESCUE · R1: что за файлы остались в общем дереве. Read-only.
#
# Повод. После снятия копии приёмка показала «общий Vault: 2 файлов, 26M».
# По коду (`batch_intake.py:58`) ZIP-пачки складываются в
# `/opt/helm-knowledge/raw-batches/<id>/original.zip` — то есть в ОБЩЕЕ
# дерево. Миграция R1 переносила `raw/health/*` и `sources/*`, про
# `raw-batches/` в ней нет ни строки.
#
# Если эти два файла — исходные архивы с медицинскими PDF, то health-
# содержимое из общего дерева не ушло, и приватность R1 закрыта только
# наполовину: база разделена, а исходники по-прежнему лежат там, где их
# видит общий контур. Утверждать PASS, не проверив это, нельзя.
set -uo pipefail

psql() { sudo docker exec helm-postgres-1 psql -U helm -d helm -tA "$@" < /dev/null; }

echo "############ 1. ЧТО ЛЕЖИТ В ОБЩЕМ ДЕРЕВЕ ############"
sudo find /opt/helm-knowledge -type f -printf '%s\t%M\t%u:%g\t%p\n' | sort -rn

echo
echo "############ 2. ПАЧКИ В БАЗЕ ############"
psql -F$'\t' -c "
  select b.id, b.status, b.archive_filename, b.archive_size_bytes,
         b.archive_raw_path,
         (select count(*) from knowledge_sources s
           where s.batch_id = b.id) as источников,
         (select count(distinct s.domain) || ':' || string_agg(distinct s.domain, ',')
            from knowledge_sources s where s.batch_id = b.id) as домены
  from knowledge_ingest_batches b
  order by b.created_at" 2>&1 | head -20

echo
echo "############ 3. ЧТО ВНУТРИ АРХИВОВ ############"
# Только имена и количество — содержимое не распаковываем и не печатаем.
while IFS= read -r z; do
  [ -n "$z" ] || continue
  echo "--- $z ---"
  echo "  файлов внутри: $(sudo unzip -Z1 "$z" 2>/dev/null | wc -l)"
  echo "  первые имена:"
  sudo unzip -Z1 "$z" 2>/dev/null | head -3 | sed 's/^/    /'
done < <(sudo find /opt/helm-knowledge -name '*.zip' -type f)

echo
echo "############ 4. ЕСТЬ ЛИ ЭТИ ЖЕ ФАЙЛЫ В ПРИВАТНОМ ДЕРЕВЕ ############"
echo "  приватных файлов: $(sudo find /opt/helm-knowledge-private -type f | wc -l)"
echo "  архивов в приватном дереве: $(sudo find /opt/helm-knowledge-private -name '*.zip' | wc -l)"

echo
echo "############ ГОТОВО ############"
