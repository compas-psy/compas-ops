#!/usr/bin/env bash
# HELM · дословно источник 48d95d41 (липидный профиль 23.08.2026).
#
# Прогон 490 расставил всё по местам: 8.1 — это 19.08.2026, а САМОЕ
# СВЕЖЕЕ измерение лежит в 48d95d41 от 23.08.2026, и там таблица
# развалилась по вертикали: «Холестерин общий» на строке 45 отдельной
# строкой, значение — где-то ещё. Именно оттуда модель взяла 8.4.
#
# Решающий вопрос, на который отвечает этот вывод: 8.4 — это значение
# ОБЩЕГО холестерина (тогда проверка отвергла верный ответ) или другого
# показателя (тогда проверка права, а сломано извлечение). Гадать
# нельзя: от ответа зависит, что чинить.
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
src = next(x for x in s.scalars(select(KnowledgeSource).where(
    KnowledgeSource.status == KnowledgeStatus.ACTIVE)).all()
    if str(x.id).startswith("48d95d41"))
print(f"источник {src.id} дата={src.content_date} парсер={src.parser}")
lines = Path(src.source_path).read_text(encoding="utf-8").splitlines()
print(f"строк: {len(lines)}\n")
for i in range(35, min(len(lines), 110)):
    print(f"{i:4d} | {lines[i]}")
PYEOF
