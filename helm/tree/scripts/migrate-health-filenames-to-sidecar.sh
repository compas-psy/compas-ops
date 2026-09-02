#!/bin/bash
# Разовая data-миграция ADR-005/P12: health-документы, заведённые ДО
# этого решения, всё ещё держат original_filename в public.knowledge_
# sources. Переносит его в health.knowledge_source_private и обнуляет в
# public — тот же код, который теперь используется для НОВЫХ health-
# загрузок, применён постфактум к уже существующим строкам.
#
# Правка 02.09.2026 (v4.0 RESCUE, шаг R1), две штуки:
#
# 1. Берутся ВСЕ health-источники, а не только те, у кого имя ещё в
#    public. Строка в сайдкаре нужна каждому независимо от имени:
#    health.knowledge_chunks.source_id ссылается на неё внешним ключом,
#    и без неё перенос чанков такого источника упадёт.
# 2. Снята ловушка expire_on_commit. Первый живой прогон 01.09 перенёс
#    ровно ОДИН источник из девяноста и встал: s.commit() в конце
#    итерации истекает ВСЕ объекты сессии, включая ещё не обработанные,
#    и следующее же обращение к source.* перечитывает строку до того,
#    как GUC установлен на новой транзакции — RLS её не показывает.
#    Теперь всё нужное вычитывается кортежами ДО цикла, и ORM-объектов,
#    которые могли бы протухнуть, в цикле нет вовсе.
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

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.tenancy import bind_knowledge_user, set_current_knowledge_user
from helm_core.models import HealthKnowledgeSourcePrivate, KnowledgeDomain, KnowledgeSource

if not health_schema_configured():
    print("health_database_url не настроен на этом сервере — "
          "прогоните scripts/setup-health-role.sh и перезапустите "
          "helm-core/helm-knowledge-worker ДО этой миграции.")
    sys.exit(1)

engine = create_engine(get_settings().database_url)
migrated, skipped = 0, 0
with Session(engine) as s:
    # knowledge_sources под FORCE ROW LEVEL SECURITY (v3.8 Фаза 1) —
    # без bind_knowledge_user() голая сессия не видит вообще ни одной
    # строки (RLS молча возвращает пусто, не ошибку). Один тенант
    # (SYSTEM_OWNER) — тот же приём, что использует probe() и весь
    # текущий код: "единственный тенант делает это неотличимым от
    # прежнего поведения" (см. probe.py::probe() docstring).
    bind_knowledge_user(s, None)
    # Кортежи, не ORM-объекты: всё, что нужно циклу, вычитано здесь и
    # больше от сессии не зависит. Именно ORM-объекты в прошлой версии
    # протухали на первом же commit() и уносили с собой весь прогон.
    rows = s.execute(
        select(KnowledgeSource.id, KnowledgeSource.knowledge_user_id,
               KnowledgeSource.original_filename)
        .where(KnowledgeSource.domain == KnowledgeDomain.HEALTH)
    ).all()
    print(f"найдено health-источников: {len(rows)}")

    for source_id, knowledge_user_id, original_filename in rows:
        with health_session(knowledge_user_id) as hs:
            already = hs.get(HealthKnowledgeSourcePrivate, source_id)
            if already is not None:
                skipped += 1
                print(f"  {source_id}: sidecar уже существует, пропущен")
                continue
            hs.add(HealthKnowledgeSourcePrivate(
                source_id=source_id, knowledge_user_id=knowledge_user_id,
                original_filename=original_filename,
            ))
        # SET LOCAL живёт до конца транзакции, а предыдущая итерация её
        # закоммитила — GUC надо ставить заново перед каждым UPDATE,
        # иначе RLS не найдёт строку и UPDATE молча обновит ноль строк.
        set_current_knowledge_user(s, knowledge_user_id)
        s.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.id == source_id)
            .values(original_filename=None)
        )
        s.commit()
        migrated += 1
        print(f"  {source_id}: перенесён")

print(f"готово: перенесено {migrated}, пропущено (уже были) {skipped}")
PY
