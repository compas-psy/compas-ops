#!/usr/bin/env bash
# HELM · как на самом деле выглядит строка холестерина в хранимом тексте.
#
# Прогон 486: модель выдала 8.4 ммоль/л, проверка отвергла его как
# «значение при чужом объекте» — значит 8.4 в таблице есть, но у другого
# показателя. Правильное значение 8.1. Чтобы читать таблицу
# детерминированно, а не уговаривать модель, надо видеть РАЗМЕТКУ строк:
# где название, где стрелка, где единица, где диапазон.
#
# ИСКАТЬ ПО ИМЕНИ ФАЙЛА НЕЛЬЗЯ (прогон 488 вернул «источник не найден»):
# у health-источников `public.knowledge_sources.original_filename` пуст,
# имя живёт в схеме health (P12). Ищем по содержимому разобранных
# файлов — это не зависит от того, где лежит имя.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF' 2>&1 | tail -90
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
    KnowledgeSource.source_path.is_not(None))).all()
print(f"активных источников с текстом: {len(sources)}")

for src in sources:
    path = Path(src.source_path)
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8")
    if "олестерин" not in text.lower():
        continue
    lines = text.splitlines()
    print(f"\n===== {src.id} домен={src.domain} парсер={src.parser} "
          f"дата={src.content_date} строк={len(lines)} =====")
    hits = [i for i, l in enumerate(lines)
            if "олестерин" in l.lower() or "8.4" in l or "8,4" in l or "8.1" in l]
    shown = set()
    for i in hits:
        for j in range(max(0, i - 2), min(len(lines), i + 3)):
            if j not in shown:
                shown.add(j)
                mark = ">>" if j in hits else "  "
                print(f"{mark} {j:4d} | {lines[j][:160]}")
        print("   ....")
PYEOF
