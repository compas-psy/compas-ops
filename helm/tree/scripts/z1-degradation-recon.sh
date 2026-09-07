#!/usr/bin/env bash
# HELM · почему все живые ответы 18:19–18:23 пришли режимом Z1
# («не нашёл прямого ответа, ближайшее…»), а не Z2. Только чтение.
#
# Z1 означает, что `synthesize_or_none()` вернул None, то есть модель
# НЕ ОТВЕТИЛА: недоступна, не помещается или не уложилась в 45 секунд.
# Прямой вызов probe в 14:24 давал Z2 на те же по типу вопросы, значит
# между 14:24 и 18:19 что-то изменилось. Главный подозреваемый —
# semantic-разбор книги из 1399 фрагментов, занявший процессор.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. НАГРУЗКА И ПАМЯТЬ ############"
uptime
free -m | head -2
echo
sudo docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null | head -12

echo
echo "############ 2. ЖИВА ЛИ МОДЕЛЬ И СКОЛЬКО ДУМАЕТ ############"
sudo docker compose ps ollama helm-knowledge-worker helm-core 2>&1 | tail -5
echo
echo "— модели в памяти:"
sudo docker compose exec -T ollama ollama ps 2>&1 | head -5
echo
echo "— прямой вызов gemma2:2b с секундомером:"
start=$(date +%s)
sudo docker compose exec -T ollama ollama run gemma2:2b "Ответь одним словом: столица Франции?" 2>&1 | head -3
echo "  заняло: $(( $(date +%s) - start )) с (порог синтеза — 45 с)"

echo
echo "############ 3. ЧТО ДЕЛАЕТ ВОРКЕР ############"
sudo docker compose logs --tail=25 helm-knowledge-worker 2>&1 | tail -25

echo
echo "############ 4. РЕЖИМЫ ПОСЛЕДНИХ ОТВЕТОВ ############"
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeAnswerRun, KnowledgeSemanticJob

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)

print("— последние 12 ответов (режим, число доказательств, время):")
for mode, count, paid, created in session.execute(
        select(KnowledgeAnswerRun.mode, KnowledgeAnswerRun.evidence_count,
               KnowledgeAnswerRun.paid_ai_used, KnowledgeAnswerRun.created_at)
        .order_by(KnowledgeAnswerRun.created_at.desc()).limit(12)).all():
    print(f"   {created} | режим={mode} | доказательств={count} | платно={paid}")

print()
print("— semantic-задания:")
for status, attempts, lease, error in session.execute(
        select(KnowledgeSemanticJob.status, KnowledgeSemanticJob.attempts,
               KnowledgeSemanticJob.lease_expires_at, KnowledgeSemanticJob.error)
        .order_by(KnowledgeSemanticJob.updated_at.desc()).limit(6)).all():
    print(f"   {status} | попыток={attempts} | аренда до {lease} | {error or '—'}")
PYEOF
