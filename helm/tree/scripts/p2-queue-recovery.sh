#!/usr/bin/env bash
# HELM · воркер убит на середине разбора — работа возвращается сама. ЗАПИСЬ.
#
# Сценарий приёмки задан владельцем 06.09.2026 дословно: остановить
# воркер после фиксации RUNNING, запустить снова и получить завершённую
# обработку без ручного SQL/backfill и без дублирования результата.
#
# Что здесь настоящее, а что ускорено. Настоящие: падение воркера
# посреди разбора (контейнер убивается), потеря владения, повторное
# взятие задания другим запуском воркера, доведение разбора до конца.
# Ускорено ровно одно: срок аренды — тридцать минут, и ждать их в
# приёмке бессмысленно, поэтому `lease_expires_at` сдвигается в прошлое
# одним UPDATE. Это имитация течения времени, а не имитация
# восстановления: сам возврат делает воркер, а не скрипт.
#
# ЕДИНСТВЕННЫЙ SQL, который здесь есть, — этот сдвиг времени. Ни статус
# задания, ни ревизия руками не трогаются: если бы трогались, проверка
# доказывала бы работу скрипта, а не системы.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

FAIL=0
psql() { sudo docker compose exec -T postgres psql -U helm -d helm -tAc "$1"; }

MARKER="queue-recovery-$(date -u +%s)"
SHARED=/opt/helm-knowledge/acceptance
sudo mkdir -p "$SHARED"

echo
echo "############ 1. ЗАДАНИЕ В ОЧЕРЕДИ ############"
# Файл кладётся в общий том: helm-core и воркер — разные контейнеры, и
# путь источника воркер читает как есть (урок приёмки P2, прогон 350).
sudo tee "$SHARED/$MARKER.md" >/dev/null <<EOF
# Проверка восстановления очереди

Маркер $MARKER. Приём от 12.03.2025, врач-терапевт Петрова Анна
Вячеславовна, жалобы на утомляемость в течение трёх месяцев.
Назначен контроль общего анализа крови через четыре недели.
EOF

JOB_ID=$(sudo docker compose exec -T helm-core python3 - <<PYEOF
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from helm_core.config import get_settings
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.semantic_jobs import enqueue_semantic
from helm_core.knowledge.tenancy import bind_knowledge_user

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)
text = open("/opt/helm-knowledge/acceptance/$MARKER.md").read()
source = ingest_text(session, domain="general", text=text,
                     original_filename="$MARKER.md")
session.flush()
job_id = enqueue_semantic(session, source_id=source.id, knowledge_user_id=tenant,
                          source_sha256=source.sha256)
session.commit()
print(job_id or "")
PYEOF
)
JOB_ID=$(echo "$JOB_ID" | tr -d '[:space:]')
echo "  задание: ${JOB_ID:-НЕ СОЗДАНО}"
[ -z "$JOB_ID" ] && { echo "  ПРОВАЛ: задание не создано"; FAIL=1; }

echo
echo "############ 2. ЖДЁМ ФИКСАЦИИ RUNNING ############"
start=$(date -u +%s)
STATE=""
until [ $(( $(date -u +%s) - start )) -ge 120 ]; do
  STATE=$(psql "select status || '|' || attempts || '|' || coalesce(lease_expires_at::text,'нет') from knowledge_semantic_jobs where id='$JOB_ID'")
  case "$STATE" in running*) break;; done*) break;; esac
  sleep 3
done
echo "  состояние: ${STATE:-пусто}"
case "$STATE" in
  running*) echo "  RUNNING зафиксирован, аренда выдана";;
  done*)    echo "  задание успело завершиться до остановки — сценарий не воспроизведён"; FAIL=1;;
  *)        echo "  ПРОВАЛ: RUNNING не наступил за 120 с"; FAIL=1;;
esac

echo
echo "############ 3. ВОРКЕР УБИТ ПОСРЕДИ РАЗБОРА ############"
sudo docker compose kill helm-knowledge-worker >/dev/null 2>&1
sudo docker compose ps helm-knowledge-worker | tail -1 | sed 's/^/  /'
AFTER_KILL=$(psql "select status from knowledge_semantic_jobs where id='$JOB_ID'")
echo "  задание после убийства: $AFTER_KILL"
[ "$AFTER_KILL" != "running" ] && { echo "  ПРОВАЛ: ожидалось running"; FAIL=1; }

RUNS_BEFORE=$(psql "select count(*) from knowledge_semantic_runs r join knowledge_sources s on s.id=r.source_id where s.original_filename='$MARKER.md'")
echo "  ревизий на этот источник: $RUNS_BEFORE"

echo
echo "############ 4. СРОК АРЕНДЫ СДВИНУТ В ПРОШЛОЕ ############"
echo "  (имитация течения тридцати минут — единственный SQL проверки)"
psql "update knowledge_semantic_jobs set lease_expires_at = now() - interval '1 minute' where id='$JOB_ID'" >/dev/null
psql "select 'аренда: ' || lease_expires_at from knowledge_semantic_jobs where id='$JOB_ID'" | sed 's/^/  /'

echo
echo "############ 5. ВОРКЕР ПОДНЯТ, БОЛЬШЕ НИЧЕГО ############"
sudo docker compose up -d helm-knowledge-worker >/dev/null 2>&1
start=$(date -u +%s)
FINAL=""
until [ $(( $(date -u +%s) - start )) -ge 300 ]; do
  FINAL=$(psql "select status || '|' || attempts from knowledge_semantic_jobs where id='$JOB_ID'")
  case "$FINAL" in done*|failed*) break;; esac
  sleep 5
done
echo "  итог задания: ${FINAL:-не изменилось}"

RUNS_AFTER=$(psql "select count(*) from knowledge_semantic_runs r join knowledge_sources s on s.id=r.source_id where s.original_filename='$MARKER.md'")
CURRENT=$(psql "select count(*) from knowledge_sources where original_filename='$MARKER.md' and current_semantic_run_id is not null")
ORPHANS=$(psql "select count(*) from knowledge_semantic_runs r join knowledge_sources s on s.id=r.source_id where s.original_filename='$MARKER.md' and r.status='running'")
echo "  ревизий стало: $RUNS_AFTER (было $RUNS_BEFORE)"
echo "  источник с текущей ревизией: $CURRENT"
echo "  ревизий, зависших в running: $ORPHANS"

echo
echo "############ ИТОГ ############"
case "$FINAL" in
  done*)   echo "  Задание доведено до конца после падения, без ручного вмешательства.";;
  failed*) echo "  ПРОВАЛ: задание закрыто как FAILED, а не выполнено"; FAIL=1;;
  *)       echo "  ПРОВАЛ: задание не сдвинулось за 300 с после подъёма воркера"; FAIL=1;;
esac
[ "$CURRENT" = "1" ] || { echo "  ПРОВАЛ: у источника нет текущей ревизии"; FAIL=1; }
[ "$ORPHANS" = "0" ] || { echo "  ПРОВАЛ: ревизия прошлой попытки осталась в running"; FAIL=1; }
case "$FINAL" in *"|2") ;; *) echo "  ПРОВАЛ: ожидалась вторая попытка, получено «$FINAL»"; FAIL=1;; esac

if [ "$FAIL" -ne 0 ]; then
  echo "############ ПРОВАЛ ############"
  exit 1
fi
echo "############ ГОТОВО ############"
