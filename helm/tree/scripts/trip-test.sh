#!/usr/bin/env bash
# HELM · как разобрались шесть голосовых и что отвечает на девять вопросов.
# Только чтение.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from datetime import date, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.probe import probe
from helm_core.knowledge.query_spec import DialogueContext
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeMemory, KnowledgeSource

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)
today = date.today()

print("############ ЧТО ЛЕГЛО СЕГОДНЯ ############")
mems = session.scalars(select(KnowledgeMemory)
                       .where(KnowledgeMemory.created_at >= today)
                       .order_by(KnowledgeMemory.created_at)).all()
for m in mems:
    n = session.scalar(select(func.count()).select_from(KnowledgeChunk)
                       .where(KnowledgeChunk.source_id == m.source_id)) if m.source_id else 0
    print(f"  память {m.created_at:%H:%M} чанков={n} {m.canonical_text[:90]!r}")
srcs = session.scalars(select(KnowledgeSource)
                       .where(KnowledgeSource.created_at >= today)
                       .order_by(KnowledgeSource.created_at)).all()
for s in srcs:
    n = session.scalar(select(func.count()).select_from(KnowledgeChunk)
                       .where(KnowledgeChunk.source_id == s.id))
    print(f"  источник {s.created_at:%H:%M} {s.domain} чанков={n} дата={s.content_date} "
          f"{(s.original_filename or '')[:40]}")
session.rollback()
bind_knowledge_user(session, tenant)

QUESTIONS = [
    ("До какого числа у меня действует загран?", None),
    ("Что я беру с собой из лекарств?", None),
    ("Сколько всего пунктов в аптечке?", None),
    ("Во сколько мне надо быть на регистрации?", None),
    ("Кто у меня страховщик?", None),
    ("А билеты?", "Кто у меня страховщик?"),
    ("Что я в прошлый раз забыл взять?", None),
    ("В каком порядке я всё делаю по прилёте?", None),
    ("Какой у меня был холестерин в последний раз?", None),
]
print("############ ОТВЕТЫ ############")
for i, (q, prev) in enumerate(QUESTIONS, start=1):
    ctx = DialogueContext(question=prev, memory=True) if prev else None
    try:
        r = probe(session, query=q, context=ctx)
    except Exception as exc:
        print(f"\n{i}. {q}\n   ПАДЕНИЕ: {type(exc).__name__}: {exc}")
        session.rollback()
        bind_knowledge_user(session, tenant)
        continue
    text = (r.answer_text or "").replace("\n", " ")[:190]
    print(f"\n{i}. {q}\n   {r.outcome}/{r.mode} :: {text}")
    session.rollback()
    bind_knowledge_user(session, tenant)
PYEOF
rc=$?
if [ "$rc" -ne 0 ]; then echo "############ ПРОВАЛ (код $rc) ############"; exit "$rc"; fi
echo "############ ГОТОВО ############"
