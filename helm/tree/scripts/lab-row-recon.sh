#!/usr/bin/env bash
# HELM · все строки корпуса, где назван холестерин, с датой источника.
#
# Прогон 489 показал формат лабораторной строки:
#   «07.10.2023  Липидный профиль (ммоль/л) Холестерин общий: 6.2»
# — дата в начале, раздел с единицей, дальше пары «показатель: значение
# (диапазон)» через запятую. Но собственный `tail -90` срезал начало
# вывода, и самый свежий источник в него не попал. Печатаем только
# строки с холестерином — их немного, обрезать не придётся.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - 2>&1 <<'PYEOF'
from pathlib import Path
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource, KnowledgeStatus

s = sessionmaker(bind=create_engine(get_settings().database_url, future=True))()
bind_knowledge_user(s, None)
sources = s.scalars(select(KnowledgeSource).where(
    KnowledgeSource.status == KnowledgeStatus.ACTIVE,
    KnowledgeSource.source_path.is_not(None))
    .order_by(KnowledgeSource.content_date)).all()
print(f"активных источников с текстом: {len(sources)}")

for src in sources:
    path = Path(src.source_path)
    if not path.is_file():
        continue
    lines = path.read_text(encoding="utf-8").splitlines()
    hits = [(i, l) for i, l in enumerate(lines) if "олестерин" in l.lower()]
    if not hits:
        continue
    print(f"\n===== {str(src.id)[:8]} дата={src.content_date} "
          f"парсер={src.parser} строк={len(lines)} =====")
    for i, line in hits:
        print(f"  {i:4d} | {line.strip()[:200]}")
PYEOF
