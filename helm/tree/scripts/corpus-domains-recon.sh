#!/usr/bin/env bash
# HELM · что вообще лежит в корпусе, кроме медицины. Чтение.
#
# Приёмка P4 задана владельцем на четырёх материалах: посещённые врачи,
# решение о подзадачах в ТЗ ШАГОВ, место работы за период из резюме,
# ответ по двум источникам. Три из четырёх — не медицина.
#
# Прежде чем строить механизм запросов, надо знать, на чём его вообще
# можно принять. Если резюме и ТЗ в корпусе нет, это выяснится сейчас, а
# не в конце работы.
#
# Печатаются домены, счётчики и имена файлов владельца — он разрешил их
# вывод в лог 06.09.2026.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Домены, объёмы и имена. Ничего не пишет."""
from collections import Counter

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import (health_schema_configured, health_session,
                                               read_original_filename)
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import HealthKnowledgeChunk, KnowledgeChunk, KnowledgeSource
from helm_core.models.base import KnowledgeStatus

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

sources = session.scalars(
    select(KnowledgeSource).where(KnowledgeSource.status == KnowledgeStatus.ACTIVE)).all()

by_domain = Counter(s.domain for s in sources)
with_semantics = Counter(s.domain for s in sources if s.current_semantic_run_id)

print("############ ДОМЕНЫ ############")
print(f"  {'домен':<16}{'источников':>12}{'с семантикой':>14}")
for domain, count in by_domain.most_common():
    print(f"  {domain:<16}{count:>12}{with_semantics[domain]:>14}")

public_chunks = session.scalar(select(func.count()).select_from(KnowledgeChunk))
health_chunks = 0
if health_schema_configured():
    with health_session(tenant) as graph:
        health_chunks = graph.scalar(select(func.count()).select_from(HealthKnowledgeChunk))
print(f"\n  чанков: public={public_chunks} health={health_chunks}")

print("\n############ ИМЕНА ФАЙЛОВ ПО НЕМЕДИЦИНСКИМ ДОМЕНАМ ############")
shown = 0
for source in sources:
    if source.domain == "health":
        continue
    name = source.original_filename or read_original_filename(
        source_id=source.id, knowledge_user_id=tenant) or "(имя не сохранено)"
    print(f"  {source.domain:<14} {name}")
    shown += 1
if not shown:
    print("  немедицинских источников нет вовсе")

print("\n############ ЕСТЬ ЛИ МАТЕРИАЛ ПОД ПРИЁМКУ P4 ############")
needles = {
    "резюме": ("резюме", "cv", "resume"),
    "ТЗ / ШАГИ": ("тз", "шаг", "spec", "требован"),
    "договор": ("договор", "contract"),
}
names = []
for source in sources:
    name = source.original_filename or read_original_filename(
        source_id=source.id, knowledge_user_id=tenant) or ""
    names.append(name.lower())
for label, keys in needles.items():
    hits = [n for n in names if any(k in n for k in keys)]
    if hits:
        print(f"  {label}: {len(hits)} — {hits[:3]}")
    else:
        print(f"  {label}: НЕ НАЙДЕНО по именам файлов")

print("\n  Оговорка: поиск по ИМЕНИ файла. Документ может лежать под другим")
print("  именем, и тогда его здесь не видно — это не доказательство отсутствия.")

session.rollback()
PYEOF
rc=$?

if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi

echo "############ ГОТОВО ############"
