#!/usr/bin/env bash
# HELM · остановить semantic-разбор, который не может завершиться, и
# вернуть машину живым ответам.
#
# ИЗМЕРЕНО (разведка 438, 15:30 UTC):
#   прямой вызов gemma2:2b «столица Франции?» → 48 секунд при пороге
#   синтеза 45; semantic-задание книги — running, аренда истекла в
#   14:47, воркер переклаивает его по кругу; режимы ответов владельца
#   18:19–18:23 — Z1, Z1, Z1, Z2, N0, тогда как до переразбора шли Z2.
#
# ПОЧЕМУ ОСТАНОВКА, А НЕ ОЖИДАНИЕ. Аренда 30 минут (`LEASE_SECONDS`),
# источник — 1399 фрагментов, модель отвечает за 48 секунд на одно
# слово. Разбор в аренду не укладывается физически: он уже трижды не
# доживал до конца. Ждать нечего — задание всё равно закончится
# `LeaseExpiredAfterMaxAttempts`, только сначала сожжёт процессор и
# испортит владельцу все ответы.
#
# Задание закрывается ЯВНОЙ причиной, а не удаляется: оно должно
# остаться видимым в очереди как незакрытый долг. Вернуть его в работу
# можно тем же способом, что и в `fb2-reparse-unblock.sh`.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ 1. ОСТАНОВИТЬ ВОРКЕР ############"
sudo docker compose stop helm-knowledge-worker

echo
echo "############ 2. ЗАКРЫТЬ НЕИСПОЛНИМОЕ ЗАДАНИЕ ############"
sudo docker compose exec -T helm-core python3 - <<'PYEOF'
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeIngestStatus, KnowledgeSemanticJob, KnowledgeSource

session = sessionmaker(bind=create_engine(get_settings().database_url, pool_pre_ping=True))()
bind_knowledge_user(session, None)

source_ids = session.scalars(
    select(KnowledgeSource.id).where(KnowledgeSource.raw_path.ilike("%.fb2"))).all()
closed = session.execute(
    update(KnowledgeSemanticJob)
    .where(KnowledgeSemanticJob.source_id.in_(source_ids),
           KnowledgeSemanticJob.status != KnowledgeIngestStatus.DONE)
    .values(status=KnowledgeIngestStatus.FAILED, lease_expires_at=None,
            error="SourceTooLargeForLease: 1399 фрагментов не укладываются в 30-минутную аренду")
    ).rowcount
session.commit()
print(f"  закрыто заданий: {closed}")

for status, attempts, error in session.execute(
        select(KnowledgeSemanticJob.status, KnowledgeSemanticJob.attempts,
               KnowledgeSemanticJob.error)
        .order_by(KnowledgeSemanticJob.updated_at.desc()).limit(5)).all():
    print(f"   {status} | попыток={attempts} | {error or '—'}")
PYEOF

echo
echo "############ 3. ЗАПУСТИТЬ ВОРКЕР ############"
sudo docker compose start helm-knowledge-worker
sleep 20

echo
echo "############ 4. МОДЕЛЬ ПОСЛЕ ОСВОБОЖДЕНИЯ ############"
uptime
start=$(date +%s)
sudo docker compose exec -T ollama ollama run gemma2:2b "Ответь одним словом: столица Франции?" 2>/dev/null | tail -1
echo "  заняло: $(( $(date +%s) - start )) с (порог синтеза — 45 с)"
