#!/usr/bin/env bash
# HELM · дошёл ли новый разбор до корпуса.
#
# После выката переразбор идёт сам: отпечаток кода не совпал с
# записанным у источника, и `reparse.py` разбирает файл заново. Здесь
# видно результат — строки с холестерином и сколько источников уже
# отмечено нынешним отпечатком.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - 2>&1 <<'PYEOF'
from pathlib import Path
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.derivation import derivation_fingerprint
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource, KnowledgeStatus

s = sessionmaker(bind=create_engine(get_settings().database_url, future=True))()
bind_knowledge_user(s, None)
current = derivation_fingerprint()
print(f"отпечаток нынешнего кода: {current[:16]}…")
total = s.scalar(select(func.count()).select_from(KnowledgeSource).where(
    KnowledgeSource.status == KnowledgeStatus.ACTIVE))
marked = s.scalar(select(func.count()).select_from(KnowledgeSource).where(
    KnowledgeSource.status == KnowledgeStatus.ACTIVE,
    KnowledgeSource.derivation_fingerprint == current))
print(f"отмечено нынешним отпечатком: {marked} из {total} активных")

print("\n--- строки с холестерином сейчас ---")
sources = s.scalars(select(KnowledgeSource).where(
    KnowledgeSource.status == KnowledgeStatus.ACTIVE,
    KnowledgeSource.source_path.is_not(None))
    .order_by(KnowledgeSource.content_date)).all()
for src in sources:
    path = Path(src.source_path)
    if not path.is_file():
        continue
    lines = path.read_text(encoding="utf-8").splitlines()
    hits = [l for l in lines if "олестерин" in l.lower()]
    if not hits:
        continue
    fresh = "отмечен" if src.derivation_fingerprint == current else "НЕ отмечен"
    print(f"\n== {str(src.id)[:8]} дата={src.content_date} {fresh} ==")
    for line in hits:
        print(f"  | {line.strip()[:180]}")
PYEOF
