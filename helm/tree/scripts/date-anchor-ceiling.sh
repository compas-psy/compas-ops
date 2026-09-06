#!/usr/bin/env bash
# HELM · сколько дат вообще можно вернуть правилом наследования. Чтение.
#
# Замер ДО реализации, а не после. Конструкция контекстных якорей задана
# владельцем 06.09.2026 взамен `document_date → occurred_at`: атом без
# своей даты может унаследовать её от якоря, но только если якорь
# однозначен — ровно один якорь роли `event` в окне.
#
# Вопрос замера один: на реальном корпусе таких окон много или единицы.
# Если единицы, вторую ступень писать не стоит, и честнее узнать это
# сейчас.
#
# Печатаются только числа: ни цитат, ни имён файлов здесь не нужно —
# вопрос количественный.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Потолок наследования дат на текущем поколении. Ничего не пишет."""
from collections import Counter

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.semantic_pilot import source_text
from helm_core.knowledge.semantic_publish import HEALTH_MODELS, PUBLIC_MODELS
from helm_core.knowledge.temporal import (ROLE_EVENT, find_date_anchors,
                                          inheritable_anchor)
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource
from helm_core.models.base import KnowledgeStatus, SemanticNodeKind

DATED_KINDS = {SemanticNodeKind.EVENT.value, SemanticNodeKind.FACT.value,
               SemanticNodeKind.DECISION.value, SemanticNodeKind.CONCEPT.value}

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

roles = Counter()
event_per_window = Counter()   # сколько окон с 0 / 1 / 2+ якорями события
dateless_total = 0
dateless_recoverable = 0
windows_total = 0
sources_seen = 0


def measure(models, graph, sources):
    global dateless_total, dateless_recoverable, windows_total, sources_seen
    for source in sources:
        text = source_text(source)
        if text is None:
            continue
        sources_seen += 1
        run_id = source.current_semantic_run_id

        windows = graph.execute(
            select(models.window.ordinal, models.window.char_start, models.window.char_end)
            .where(models.window.semantic_run_id == run_id)).all()

        # Датируемые узлы БЕЗ даты, разложенные по окнам через упоминания.
        rows = graph.execute(
            select(models.mention.window_id, models.node.id)
            .join(models.node, models.node.id == models.mention.node_id)
            .where(models.node.semantic_run_id == run_id,
                   models.node.kind.in_(DATED_KINDS),
                   models.node.occurred_at_start.is_(None))).all()
        by_window = Counter()
        seen_nodes = set()
        for window_id, node_id in rows:
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            by_window[window_id] += 1
        dateless_total += len(seen_nodes)

        for ordinal, start, end in windows:
            windows_total += 1
            anchors = find_date_anchors(text[start:end])
            for anchor in anchors:
                roles[anchor.role] += 1
            events = sum(1 for a in anchors if a.role == ROLE_EVENT)
            event_per_window["0" if events == 0 else "1" if events == 1 else "2+"] += 1
            if inheritable_anchor(anchors) is not None:
                dateless_recoverable += by_window.get(ordinal, 0)


public_sources = session.scalars(
    select(KnowledgeSource).where(KnowledgeSource.status == KnowledgeStatus.ACTIVE,
                                  KnowledgeSource.current_semantic_run_id.is_not(None))).all()
health_sources = [s for s in public_sources if s.domain == "health"]
other_sources = [s for s in public_sources if s.domain != "health"]

measure(PUBLIC_MODELS, session, other_sources)
if health_sources and health_schema_configured():
    with health_session(tenant) as graph:
        measure(HEALTH_MODELS, graph, health_sources)

print("############ ПОТОЛОК НАСЛЕДОВАНИЯ ДАТ ############")
print(f"  источников с текстом:        {sources_seen}")
print(f"  окон:                        {windows_total}")
print(f"  якорей по ролям:             {dict(roles)}")
print(f"  окон по числу якорей event:  {dict(event_per_window)}")
print(f"  датируемых узлов без даты:   {dateless_total}")
print(f"  из них в окнах с одним event: {dateless_recoverable}")
if dateless_total:
    share = 100.0 * dateless_recoverable / dateless_total
    print(f"  доля восстановимых:          {share:.1f}%")
print()
print("  Это ПОТОЛОК, а не результат: правило наследования ещё не")
print("  написано, и часть этих узлов может оказаться не событием, а")
print("  свойством, которому дата приёма не принадлежит.")

session.rollback()
PYEOF

echo "############ ГОТОВО ############"
