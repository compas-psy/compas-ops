#!/usr/bin/env bash
# HELM · как на самом деле выглядит строка холестерина в хранимом тексте.
#
# Прогон 486: модель выдала 8.4 ммоль/л, проверка отвергла его как
# «значение при чужом объекте» — значит 8.4 в таблице есть, но у другого
# показателя. Правильное значение 8.1. Чтобы читать таблицу
# детерминированно, а не уговаривать модель, надо видеть РАЗМЕТКУ строк:
# где название, где стрелка, где единица, где диапазон.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF' 2>&1 | tail -80
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource

s = sessionmaker(bind=create_engine(get_settings().database_url, future=True))()
bind_knowledge_user(s, None)
src = s.scalars(select(KnowledgeSource).where(
    KnowledgeSource.original_filename.like("%иохими%"))).first()
if src is None:
    print("источник не найден")
    raise SystemExit

print(f"источник: {src.original_filename}")
print(f"парсер: {src.parser}  путь: {src.source_path}")
from pathlib import Path
text = Path(src.source_path).read_text(encoding="utf-8")
lines = text.splitlines()
print(f"строк в тексте: {len(lines)}")

hits = [i for i, l in enumerate(lines)
        if "олестерин" in l.lower() or "8.4" in l or "8,4" in l or "8.1" in l]
print(f"\n--- строки с холестерином / 8.1 / 8.4 и их соседи ---")
shown = set()
for i in hits:
    for j in range(max(0, i - 2), min(len(lines), i + 3)):
        if j not in shown:
            shown.add(j)
            mark = ">>" if j in hits else "  "
            print(f"{mark} {j:4d} | {lines[j]}")
    print("   ....")
PYEOF
