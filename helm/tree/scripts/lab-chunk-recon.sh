#!/usr/bin/env bash
# HELM · почему число холестерина не доходит до ответа. Только чтение.
#
# Прогон 478 показал: в разобранном тексте после переразбора стоит
# «Холестерин общий ↑ 8.1 ммоль/л» — восстановление таблицы сработало.
# Значит дефект дальше по пути: либо чанка с этой строкой нет, либо он
# есть и проигрывает в поиске. Здесь различается одно от другого.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from pathlib import Path

from sqlalchemy import create_engine, select, text as sql
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.probe import _lexical_search
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource, KnowledgeStatus

session = sessionmaker(bind=create_engine(get_settings().database_url, future=True))()
tenant = bind_knowledge_user(session, None)
print(f"  health-схема настроена: {health_schema_configured()}")

targets = []
for source in session.scalars(
        select(KnowledgeSource).where(KnowledgeSource.status == KnowledgeStatus.ACTIVE)).all():
    path = Path(source.source_path or "")
    if path.is_file() and "олестерин" in path.read_text(encoding="utf-8"):
        targets.append(source)
print(f"  источников с холестерином в тексте: {len(targets)}")

print()
print("############ ЧАНКИ ЭТИХ ИСТОЧНИКОВ В HEALTH-СХЕМЕ ############")
with health_session(tenant) as hs:
    for source in targets:
        rows = hs.execute(sql(
            "select ordinal, text from health.knowledge_chunks "
            "where source_id = :sid order by ordinal"), {"sid": str(source.id)}).all()
        hits = [r for r in rows if "олестерин" in (r.text or "")]
        print(f"  ── {source.id} · чанков {len(rows)} · с холестерином {len(hits)}")
        for row in hits[:2]:
            print(f"     ~ [{row.ordinal}] {(row.text or '').strip()[:240]}")

print()
print("############ ЧТО НАХОДИТ ПОИСК ############")
for query in ("какой у меня был холестерин в последний раз?", "холестерин общий"):
    print(f"  запрос: {query}")
    hits = _lexical_search(session, query=query, domain=None,
                           knowledge_user_id=tenant, source_ids=())
    for hit in hits[:5]:
        head = (hit.chunk_text or "").strip().replace("\n", " ")[:130]
        print(f"    ранг {hit.rank:.4f} · {hit.original_filename or hit.source_id}")
        print(f"      {head}")
    if not hits:
        print("    лексика не нашла ничего")
PYEOF
