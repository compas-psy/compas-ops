#!/usr/bin/env bash
# HELM · почему «Запомни ссылки на мои каналы» → «Дай ссылку на канал B17»
# ответил «в ваших записях я такого не нашёл». Только чтение.
#
# Владелец 06.09.2026: «Не объявляй причиной плохую модель или
# отсутствие данных, пока не проверена эта цепочка». Здесь проверяется
# ровно она: есть ли запись, под кем, попадает ли в поиск, каким рангом,
# дошло ли содержание до общего слоя (источники/чанки/граф).
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Цепочка «Запомни → извлеки» на живых данных. Ничего не пишет."""
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.probe import probe
from helm_core.knowledge.recall import build_or_tsquery, search_memories
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeChunk, KnowledgeMemory, KnowledgeSource
from helm_core.models.base import utcnow

QUESTION = "Дай ссылку на мой канал B17"

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)
print(f"тенант: {tenant}")

print("\n############ 1. ЕСТЬ ЛИ ЗАПИСЬ ############")
rows = session.execute(
    select(KnowledgeMemory.id, KnowledgeMemory.created_at, KnowledgeMemory.status,
           KnowledgeMemory.kind, KnowledgeMemory.expires_at,
           func.length(KnowledgeMemory.canonical_text),
           KnowledgeMemory.knowledge_user_id)
    .order_by(KnowledgeMemory.created_at.desc()).limit(10)).all()
print(f"  последних записей памяти: {len(rows)}")
for mid, created, status, kind, expires, length, owner in rows:
    print(f"  {created:%d.%m %H:%M} {status:9} {kind:10} длина={length:5} "
          f"истекает={expires} тенант={'свой' if owner == tenant else owner}")

b17 = session.scalars(
    select(KnowledgeMemory).where(KnowledgeMemory.canonical_text.ilike("%b17%"))).all()
print(f"\n  записей памяти со словом b17: {len(b17)}")
for m in b17:
    print(f"    id={m.id} статус={m.status} длина={len(m.canonical_text)} "
          f"создана={m.created_at:%d.%m %H:%M} истекает={m.expires_at}")
    print(f"    первые 120 символов: {m.canonical_text[:120]!r}")

print("\n############ 2. ПОПАДАЕТ ЛИ ЗАПИСЬ В ПОИСК ############")
tsquery = build_or_tsquery(QUESTION)
print(f"  вопрос: {QUESTION!r}")
for m in b17:
    row = session.execute(
        select(KnowledgeMemory.tsv.op("@@")(tsquery),
               func.ts_rank(KnowledgeMemory.tsv, tsquery, 2),
               func.ts_rank(KnowledgeMemory.tsv, tsquery, 0))
        .where(KnowledgeMemory.id == m.id)).one()
    print(f"    совпадает={row[0]}  ранг(norm=2, длина делит)={row[1]:.6f}  "
          f"ранг(norm=0)={row[2]:.6f}   порог памяти=0.003")
    # Какие вообще леммы вопроса нашлись в записи — без этого «не
    # совпало» ничего не объясняет.
    lex = session.execute(text(
        "select word from ts_stat($$select tsv from knowledge_memories "
        "where id = :mid$$) limit 200").bindparams(mid=str(m.id))).all()
    words = {w[0] for w in lex}
    asked = session.execute(text(
        "select lexeme from unnest(to_tsvector('russian', :q)) ")
        .bindparams(q=QUESTION)).all()
    asked = {a[0] for a in asked}
    print(f"    леммы вопроса: {sorted(asked)}")
    print(f"    из них есть в записи: {sorted(asked & words)}")
    print(f"    есть ли 'b17' в леммах записи: {'b17' in words}")
    print(f"    похожие леммы записи: {sorted(w for w in words if 'b17' in w)}")

hits = search_memories(session, query=QUESTION, knowledge_user_id=tenant, now=utcnow())
print(f"\n  search_memories вернул: {len(hits)}")
for h in hits:
    print(f"    ранг={h.rank:.6f} {h.canonical_text[:60]!r}")

print("\n############ 3. ДОШЛО ЛИ СОДЕРЖАНИЕ ДО ОБЩЕГО СЛОЯ ############")
src = session.scalars(select(KnowledgeSource).where(
    KnowledgeSource.original_filename.ilike("%канал%"))).all()
print(f"  источников с «канал» в имени: {len(src)}")
chunks = session.execute(
    select(func.count()).select_from(KnowledgeChunk)
    .where(KnowledgeChunk.text.ilike("%b17%"))).scalar()
print(f"  чанков со словом b17: {chunks}")
print("  (если 0 — «Запомни» не участвует в общей обработке: своя таблица,")
print("   свой поиск, ни чанков, ни узлов графа)")

print("\n############ 4. ЧТО ОТВЕТИТ PROBE СЕЙЧАС ############")
result = probe(session, query=QUESTION)
print(f"  исход: {result.outcome}  режим: {result.mode}")
print(f"  ответ: {(result.answer_text or '')[:300]}")
session.rollback()
PYEOF
rc=$?

if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi

echo "############ ГОТОВО ############"
