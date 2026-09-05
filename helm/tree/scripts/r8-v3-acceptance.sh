#!/bin/bash
# HELM · приёмка R8-v3 и corpus-quality checkpoint.
#
# action=recon: не пишет ничего, модель не вызывает.
#
# Владелец 05.09.2026: «сначала доказать: todo=0, FAILED=0, coverage
# каждого current run = 1.0, все 90 current_semantic_run_id указывают
# именно на semantic version 3. И только после этого r6-rebuild.sh».
#
# И отдельно: «после R6 idempotence PASS нужен короткий corpus-quality
# checkpoint… иначе можем технически получить великолепно идемпотентный,
# но семантически бедный граф».
#
# Оба блока здесь, потому что считаются по одному и тому же срезу.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Приёмка переноса и качество корпуса. Только чтение."""
from collections import Counter

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.backfill import SEMANTIC_VERSION, plan
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.semantic_publish import HEALTH_MODELS, PUBLIC_MODELS
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSemanticRun, KnowledgeSource
from helm_core.models.base import SemanticNodeKind, SemanticRunStatus
from helm_core.models.health_tables import HealthKnowledgeSourcePrivate

#: Виды, у которых дата вообще бывает. У ENTITY её нет по конструкции:
#: `parse_occurred_at()` зовётся только в атомном пути. Считать их вместе
#: — та самая ошибка, из-за которой пришлось отзывать «955 из 956».
DATED_KINDS = {SemanticNodeKind.EVENT.value, SemanticNodeKind.FACT.value,
               SemanticNodeKind.DECISION.value, SemanticNodeKind.CONCEPT.value}

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
with sessionmaker(engine, expire_on_commit=False)() as session:
    tenant = bind_knowledge_user(session, None)

    print(f"############ 1. ПРИЁМКА R8-v{SEMANTIC_VERSION} ############")
    remaining = plan(session, knowledge_user_id=tenant)
    print(f"  источников всего:        {remaining['sources_total']}")
    print(f"  разобрано текущей версией: {remaining['already_current']}")
    print(f"  осталось (todo):         {remaining['todo']}")
    print(f"  без текста:              {remaining['no_text']}")

    sources = session.execute(
        select(KnowledgeSource).where(KnowledgeSource.knowledge_user_id == tenant)
    ).scalars().all()

    names = {}
    if health_schema_configured():
        with health_session(tenant) as graph:
            names = {row.source_id: row.original_filename
                     for row in graph.execute(
                         select(HealthKnowledgeSourcePrivate)).scalars().all()}

    wrong_version, not_ready, low_coverage, failed_windows, no_run = [], [], [], [], []
    current_runs = {}
    for source in sources:
        title = names.get(source.id) or str(source.id)[:8]
        if not source.current_semantic_run_id:
            no_run.append(title)
            continue
        run = session.get(KnowledgeSemanticRun, source.current_semantic_run_id)
        current_runs[source.id] = run
        if run.semantic_version != SEMANTIC_VERSION:
            wrong_version.append(f"{title}: версия {run.semantic_version}")
        if run.status != SemanticRunStatus.READY:
            not_ready.append(f"{title}: {run.status}")
        if run.coverage_ratio is None or float(run.coverage_ratio) < 1.0:
            low_coverage.append(f"{title}: coverage {run.coverage_ratio}")
        if run.windows_failed:
            failed_windows.append(f"{title}: окон провалено {run.windows_failed}")

    def verdict(name: str, offenders: list[str]) -> bool:
        mark = "ОК" if not offenders else f"НЕТ ({len(offenders)})"
        print(f"  {name:<44} {mark}")
        for line in offenders[:10]:
            print(f"      {line}")
        return not offenders

    print()
    passed = all([
        verdict("todo == 0", [] if remaining["todo"] == 0 else [f"осталось {remaining['todo']}"]),
        verdict("у каждого источника есть текущая ревизия", no_run),
        verdict(f"все текущие ревизии версии {SEMANTIC_VERSION}", wrong_version),
        verdict("все текущие ревизии READY", not_ready),
        verdict("coverage каждой текущей ревизии = 1.0", low_coverage),
        verdict("ни одного проваленного окна", failed_windows),
    ])
    print()
    print(f"  ПРИЁМКА R8-v{SEMANTIC_VERSION}: {'PASS' if passed else 'НЕ ПРОЙДЕНА'}")

    print()
    print("############ 2. CORPUS-QUALITY CHECKPOINT ############")
    current_ids = {run.id for run in current_runs.values()}

    def count_nodes(graph, models) -> dict:
        stats = {"текущие": Counter(), "прежние": Counter(),
                 "точность": Counter(), "с датой": 0, "датируемых": 0}
        for node in graph.execute(select(models.node).where(
                models.node.knowledge_user_id == tenant)).scalars().all():
            bucket = "текущие" if node.semantic_run_id in current_ids else "прежние"
            stats[bucket][node.kind] += 1
            if bucket != "текущие" or node.kind not in DATED_KINDS:
                continue
            stats["датируемых"] += 1
            stats["точность"][node.date_precision or "поле пусто"] += 1
            if node.occurred_at_start is not None:
                stats["с датой"] += 1
        return stats

    scopes = [("public", session, PUBLIC_MODELS)]
    if health_schema_configured():
        scopes.append(("health", None, HEALTH_MODELS))

    for scope, graph, models in scopes:
        if graph is None:
            with health_session(tenant) as opened:
                stats = count_nodes(opened, models)
        else:
            stats = count_nodes(graph, models)
        current_total = sum(stats["текущие"].values())
        older_total = sum(stats["прежние"].values())
        print(f"  --- {scope} ---")
        print(f"  узлов текущего поколения:  {current_total}")
        print(f"  узлов прежних поколений:   {older_total} (в ответах не участвуют)")
        print(f"  по видам (текущие):        {dict(stats['текущие'])}")
        print(f"  датируемых узлов:          {stats['датируемых']}")
        print(f"  из них с occurred_at:      {stats['с датой']}")
        print(f"  точность:                  {dict(stats['точность'])}")

    print()
    print("  Чего этот срез НЕ говорит: сколько дат сняло grounding. В базе")
    print("  лежит только `rejected_count` на окно — общее число отказов без")
    print("  разбивки по причине. Отделить дату от evidence и реестра видов")
    print("  можно лишь на замере (`date-extraction-probe.sh`) либо заведя")
    print("  отдельные счётчики в ревизии. Второе — отдельная правка, и она")
    print("  требует ещё одного пересчёта, поэтому не делается на ходу.")
    session.rollback()
PYEOF

echo "############ ГОТОВО ############"
