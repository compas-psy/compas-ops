#!/usr/bin/env bash
# HELM · два вопроса одним обращением. Только чтение.
#
#   1. Что лежит в тексте биохимии после переразбора — есть ли число
#      холестерина рядом с его названием (задача 3e).
#   2. Чего не хватает Forgejo, чтобы его активировать: админ, PAT,
#      организация, репозитории (задача пункта 6).
#
# Секреты не печатаются — только наличие файлов.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ 1. БИОХИМИЯ: ЧТО В ТЕКСТЕ ПОСЛЕ ПЕРЕРАЗБОРА ############"
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeSource, KnowledgeStatus

session = sessionmaker(bind=create_engine(get_settings().database_url, future=True))()
bind_knowledge_user(session, None)

# Имя health-источника лежит в health-схеме, поэтому ищем по всем живым
# источникам и опознаём биохимию по содержимому разобранного текста.
found = []
for source in session.scalars(
        select(KnowledgeSource).where(KnowledgeSource.status == KnowledgeStatus.ACTIVE)).all():
    path = Path(source.source_path or "")
    if not path.is_file():
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        continue
    if "олестерин" in text and ("иохим" in text or "ммоль" in text):
        found.append((source, text))

print(f"  источников с холестерином и лабораторными единицами: {len(found)}")
for source, text in found[:3]:
    print(f"  ── {source.original_filename or source.id} · парсер {source.parser}"
          f" · отпечаток {(source.derivation_fingerprint or 'NULL')[:12]}")
    lines = [line for line in text.splitlines() if "олестерин" in line]
    print(f"     строк со словом «холестерин» в разобранном тексте: {len(lines)}")
    for line in lines[:6]:
        print(f"     | {line.strip()[:160]}")
    chunks = session.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id)).all()
    hits = [c for c in chunks if "олестерин" in c.text]
    print(f"     чанков всего {len(chunks)}, из них с холестерином {len(hits)}")
    for chunk in hits[:2]:
        print(f"     ~ {chunk.text.strip()[:220]}")
PYEOF

echo
echo "############ 2. FORGEJO: ЧЕГО НЕ ХВАТАЕТ ДЛЯ АКТИВАЦИИ ############"
echo "— учётные записи:"
sudo docker compose exec -T -u git forgejo sh -c 'forgejo admin user list 2>&1' | head -8 | sed 's/^/  /'
echo "— PAT для миграции (github_mirror_pat):"
if sudo test -f /etc/helm/secrets/github_mirror_pat; then
  echo "  ЕСТЬ ($(sudo stat -c '%A %U:%G' /etc/helm/secrets/github_mirror_pat))"
else
  echo "  НЕТ — приватные репозитории и push mirror недоступны"
fi
echo "— организация и репозитории в Forgejo:"
sudo docker compose exec -T -u git forgejo sh -c 'ls -1 /data/git/repositories 2>&1' | head -10 | sed 's/^/  /'
echo "— скрипты миграции на месте:"
for f in /opt/helm/scripts/forgejo-migrate.py; do
  sudo test -f "$f" && echo "  $f: есть" || echo "  $f: НЕТ"
done
