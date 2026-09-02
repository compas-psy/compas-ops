#!/bin/bash
# ADR-019 фаза 4: "прогнать backfill на небольшом реальном наборе, проверить
# качество атомизации/relations вручную, ПРЕЖДЕ ЧЕМ катить на весь корпус"
# (распоряжение владельца). Здесь — САМЫЙ первый шаг этого: реальный вызов
# atomize_and_store() на нескольких настоящих health-источниках, реальный
# Ollama (gemma2:2b), но без единого коммита — цель увидеть, что именно
# 2b-модель извлекает из настоящего русского медицинского текста, прежде
# чем доверять ей запись в Second Brain по всему корпусу.
#
# ВАЖНО: domain для самого вызова atomize_and_store() ниже — "personal", не
# "health", хотя ТЕКСТ читается из настоящих health-источников. Не ошибка: для
# health atomizer.py пишет через health_schema.write_notes()/write_relations()
# — они открывают СОБСТВЕННОЕ соединение и коммитят немедленно (см. докстринг
# health_schema.py, "Короткоживущая сессия... Коммитит на успешном выходе") —
# внешний session.rollback() их не откатывает. "personal" держит запись в
# public той же сессии s, которую мы гарантированно откатываем в конце —
# единственный способ увидеть вывод модели на реальном тексте, ничего не
# закоммитив. Маршрутизация в health.* уже отдельно проверена юнит-тестами
# (test_knowledge_health_isolation.py) — здесь проверяется НЕ она.
#
# НАЙДЕНО этим же заходом (health-chunks-location-check.sh): все 90
# health-источников физически лежат текстом в public.knowledge_chunks, НИ
# ОДИН — в health.knowledge_chunks. Они загружены ДО первого прогона
# setup-health-role.sh; migrate-health-filenames-to-sidecar.sh перенёс
# только original_filename, не сам текст (P12 деплой не завершён — задача
# #39, отдельная от этого ADR). Читаем текст оттуда, где он РЕАЛЬНО лежит
# сегодня — public.knowledge_chunks, не health.knowledge_chunks.
#
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail

cd /opt/helm/compose
sudo docker compose exec -T helm-core python3 <<'PY'
import tempfile

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from helm_core.config import get_settings
from helm_core.knowledge.atomizer import atomize_and_store
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeNote, KnowledgeRelation, KnowledgeSource

N = 3

engine = create_engine(get_settings().database_url)

with Session(engine) as s, tempfile.TemporaryDirectory() as vault_root:
    knowledge_user_id = bind_knowledge_user(s, None)
    sources = s.scalars(
        select(KnowledgeSource)
        .where(KnowledgeSource.domain == "health")
        .order_by(KnowledgeSource.created_at)
        .limit(N)
    ).all()
    print(f"взято {len(sources)} health-источников для пробного прогона (без коммита)")

    for source in sources:
        rows = s.scalars(
            select(KnowledgeChunk.text)
            .where(KnowledgeChunk.source_id == source.id)
            .order_by(KnowledgeChunk.ordinal)
        ).all()
        text = "\n\n".join(rows)
        print(f"\n=== source={source.id} chunks={len(rows)} chars={len(text)} ===")

        count = atomize_and_store(
            s, domain="personal", knowledge_user_id=knowledge_user_id,
            source_id=source.id, source_sha256=source.sha256, text=text,
            vault_root=vault_root,
        )
        print(f"атомов создано/обновлено: {count}")

    print("\n=== заметки (public.knowledge_notes, ещё не закоммичено) ===")
    notes = s.scalars(select(KnowledgeNote)).all()
    for note in notes:
        print(f"  slug={note.slug!r} type={note.type} sources={len(note.source_ids or [])}")
    print("--- relations ---")
    for rel in s.scalars(select(KnowledgeRelation)).all():
        print(f"  {rel.from_id!r} --{rel.relation_type}--> {rel.to_id!r} ({rel.evidence_type})")

    print("\n=== реально сгенерированные .md-файлы (в tmp, никогда в /opt/helm-knowledge) ===")
    for note in notes:
        print(f"\n--- {note.file_path} ---")
        print(open(note.file_path, encoding="utf-8").read())

    s.rollback()
    print("\nоткачено — ничего не осталось ни в public, ни на диске "
          "(health.* этим прогоном не трогалась вообще)")
PY
