#!/bin/bash
# Разовая data-миграция ADR-005/P12: 4 уже существующих живых health-
# документа заведены ДО этого решения — их original_filename всё ещё
# сидит в public.knowledge_sources. Переносит его в health.knowledge_
# source_private и обнуляет в public — тот же код (health_schema.py::
# write_original_filename), который теперь используется для НОВЫХ
# health-загрузок, здесь просто применён постфактум к уже существующим
# строкам.
#
# Требует scripts/setup-health-role.sh уже прогнанным (секрет заполнен,
# health.* таблицы существуют) И helm-core уже перезапущенным с новым
# секретом — иначе health_schema_configured() внутри контейнера видит
# пустую строку и скрипт откажется работать (fail-closed, не тихая
# деградация: это МИГРАЦИЯ данных, тихий no-op здесь хуже явной ошибки).
#
# Идемпотентен: повторный прогон пропускает source, для которого sidecar
# уже существует (проверяется по PRIMARY KEY, ON CONFLICT DO NOTHING).
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail
cd /opt/helm/compose || exit 1

echo '=== ADR-005/P12: перенос original_filename health-источников в sidecar ==='
sudo docker compose exec -T helm-core python3 <<'PY'
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.models import HealthKnowledgeSourcePrivate, KnowledgeDomain, KnowledgeSource

if not health_schema_configured():
    print("health_database_url не настроен на этом сервере — "
          "прогоните scripts/setup-health-role.sh и перезапустите "
          "helm-core/helm-knowledge-worker ДО этой миграции.")
    sys.exit(1)

engine = create_engine(get_settings().database_url)
migrated, skipped = 0, 0
with Session(engine) as s:
    sources = s.scalars(
        select(KnowledgeSource).where(
            KnowledgeSource.domain == KnowledgeDomain.HEALTH,
            KnowledgeSource.original_filename.isnot(None),
        )
    ).all()
    print(f"найдено health-источников с именем файла в public: {len(sources)}")

    for source in sources:
        with health_session(source.knowledge_user_id) as hs:
            already = hs.get(HealthKnowledgeSourcePrivate, source.id)
            if already is not None:
                skipped += 1
                print(f"  {source.id}: sidecar уже существует, пропущен")
                continue
            hs.add(HealthKnowledgeSourcePrivate(
                source_id=source.id, knowledge_user_id=source.knowledge_user_id,
                original_filename=source.original_filename,
            ))
        source.original_filename = None
        s.commit()
        migrated += 1
        print(f"  {source.id}: перенесён")

print(f"готово: перенесено {migrated}, пропущено (уже были) {skipped}")
PY
