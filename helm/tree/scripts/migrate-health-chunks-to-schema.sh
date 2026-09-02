#!/bin/bash
# HELM v4.0 RESCUE · R1: перенос текста и эмбеддингов health-источников
# из public.knowledge_chunks в health.knowledge_chunks (§14.16, §30.8.5 C).
#
# Находка R0: все 90 health-источников физически лежат текстом в общей
# схеме — 953 чанка, 207 346 символов, в health-схеме ноль. Документы
# загружены до включения изоляции, а прошлая миграция переносила только
# имя файла.
#
# КОПИРУЕТ, НЕ ПЕРЕМЕЩАЕТ. Решение владельца 02.09.2026: удаление из
# public — отдельный шаг и только после успешного бэкапа. Внешнее
# хранилище точек возврата сейчас не работает, а §14.16 ставит бэкап
# первым шагом именно перед необратимой частью.
#
# Требует, чтобы сайдкар был заполнен для ВСЕХ health-источников:
# health.knowledge_chunks.source_id ссылается на него внешним ключом.
# Сначала migrate-health-filenames-to-sidecar.sh, потом этот.
#
# Идемпотентен: источник, у которого в health уже столько же чанков,
# сколько в public, пропускается целиком.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo '=== R1: перенос health-чанков в health-схему ==='
sudo docker compose exec -T helm-core python3 <<'PY'
import hashlib
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import (
    health_schema_configured, health_session, write_chunks,
)
from helm_core.knowledge.tenancy import bind_knowledge_user, set_current_knowledge_user
from helm_core.models import (
    HealthKnowledgeChunk, HealthKnowledgeSourcePrivate,
    KnowledgeChunk, KnowledgeDomain, KnowledgeSource,
)

if not health_schema_configured():
    print("health_database_url не настроен в этом процессе — миграция данных "
          "не имеет права молча деградировать. Прогоните setup-health-role.sh "
          "и перезапустите helm-core/helm-knowledge-worker.")
    sys.exit(1)


def digest(texts):
    return hashlib.md5("\n".join(texts).encode("utf-8")).hexdigest()


engine = create_engine(get_settings().database_url)
moved_sources = skipped = failed = 0
moved_chunks = 0

with Session(engine) as s:
    bind_knowledge_user(s, None)
    # Кортежи, не ORM-объекты: commit() внутри цикла истекает объекты, а
    # перечитывание без GUC текущей транзакции RLS не пропустит. Тот же
    # класс ошибки, что остановил migrate-health-filenames-to-sidecar.sh
    # на первом же источнике.
    rows = s.execute(
        select(KnowledgeSource.id, KnowledgeSource.knowledge_user_id)
        .where(KnowledgeSource.domain == KnowledgeDomain.HEALTH)
        .order_by(KnowledgeSource.id)
    ).all()
    print(f"health-источников: {len(rows)}")

    for source_id, knowledge_user_id in rows:
        set_current_knowledge_user(s, knowledge_user_id)
        public_rows = s.execute(
            select(KnowledgeChunk.ordinal, KnowledgeChunk.text, KnowledgeChunk.embedding)
            .where(KnowledgeChunk.source_id == source_id)
            .order_by(KnowledgeChunk.ordinal)
        ).all()
        if not public_rows:
            print(f"  {source_id}: в public чанков нет, нечего переносить")
            skipped += 1
            continue

        ordinals = [r.ordinal for r in public_rows]
        if ordinals != list(range(len(ordinals))):
            # write_chunks() расставляет ordinal по позиции в списке. Если
            # в public нумерация не сплошная с нуля, перенос через него
            # тихо переименует чанки — лучше отказаться и разобраться.
            print(f"  {source_id}: ОТКАЗ, ordinal не сплошной с нуля: {ordinals[:5]}...")
            failed += 1
            continue

        with health_session(knowledge_user_id) as hs:
            if hs.get(HealthKnowledgeSourcePrivate, source_id) is None:
                print(f"  {source_id}: ОТКАЗ, нет строки в сайдкаре — "
                      f"сначала migrate-health-filenames-to-sidecar.sh")
                failed += 1
                continue
            already = hs.scalar(
                select(func.count()).select_from(HealthKnowledgeChunk)
                .where(HealthKnowledgeChunk.source_id == source_id)
            ) or 0
        if already == len(public_rows):
            print(f"  {source_id}: уже перенесён ({already} чанков), пропущен")
            skipped += 1
            continue
        if already:
            print(f"  {source_id}: ОТКАЗ, в health {already} чанков против "
                  f"{len(public_rows)} в public — частичный перенос, разберитесь руками")
            failed += 1
            continue

        texts = [r.text for r in public_rows]
        embeddings = [None if r.embedding is None else list(r.embedding) for r in public_rows]
        written = write_chunks(
            source_id=source_id, knowledge_user_id=knowledge_user_id,
            chunks=texts, embeddings=embeddings,
        )

        with health_session(knowledge_user_id) as hs:
            health_rows = hs.execute(
                select(HealthKnowledgeChunk.text, HealthKnowledgeChunk.embedding)
                .where(HealthKnowledgeChunk.source_id == source_id)
                .order_by(HealthKnowledgeChunk.ordinal)
            ).all()
        health_texts = [r.text for r in health_rows]
        vec_public = sum(1 for e in embeddings if e is not None)
        vec_health = sum(1 for r in health_rows if r.embedding is not None)
        ok = (digest(texts) == digest(health_texts)) and vec_public == vec_health
        print(f"  {source_id}: {written} чанков, векторов {vec_public}→{vec_health}, "
              f"отпечаток {'совпал' if ok else 'РАЗОШЁЛСЯ'}")
        if not ok:
            failed += 1
            continue
        moved_sources += 1
        moved_chunks += written

print(f"готово: источников перенесено {moved_sources} ({moved_chunks} чанков), "
      f"пропущено {skipped}, отказов {failed}")
sys.exit(1 if failed else 0)
PY
