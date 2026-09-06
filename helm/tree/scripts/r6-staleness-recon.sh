#!/usr/bin/env bash
# HELM · почему структурный ответ опустел после semantic-v3. Только чтение.
#
# Факт (прогон 344, 06.09.2026): на «каких врачей я посещал?» пришёл
# ровно NOT_FOUND — 47 символов. Утром того же дня приходил список из
# пяти врачей. Между этими ответами произошло одно событие: пересчёт
# корпуса на semantic-v3 переключил текущие ревизии у всех 90
# источников.
#
# Гипотеза: слой личностей R6 построен над узлами ПРЕЖНЕГО поколения.
# `query_router` берёт узлы текущей ревизии, а их ни одна личность не
# знает — ответ пуст при нетронутых данных.
#
# Проверяется не пересказом, а встроенной инструментовкой самого
# ответа: `considered` и `skipped` заведены в `DoctorsAnswer` ровно для
# вопроса «почему пусто» (query_router.py:177-183). Имена и подписи не
# печатаются: вопрос количественный.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Разбор пустого структурного ответа. Ничего не пишет."""
import json

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.query_router import answer_doctors_visited
from helm_core.knowledge.semantic_publish import HEALTH_MODELS, PUBLIC_MODELS
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource

engine = create_engine(get_settings().database_url)
session = sessionmaker(bind=engine)()
bind_knowledge_user(session, None)

print("############ 1. ОТВЕТ СВОИМИ СЛОВАМИ ############")
answer = answer_doctors_visited(session, question="каких врачей я посещал?",
                                knowledge_user_id=None)
print(f"  path_used:   {answer.path_used}")
print(f"  graph_edges: {answer.graph_edges}")
print(f"  items:       {len(answer.items)}")
print(f"  considered:  {json.dumps(answer.considered, ensure_ascii=False)}")
print(f"  skipped:     {json.dumps(answer.skipped, ensure_ascii=False)}")

print()
print("############ 2. ПОКОЛЕНИЯ И ЛИЧНОСТИ ############")
current = {row[0] for row in session.execute(
    select(KnowledgeSource.current_semantic_run_id)
    .where(KnowledgeSource.current_semantic_run_id.is_not(None))).all()}
print(f"  источников с текущей ревизией: {len(current)}")


def report(name, models, sess):
    total = sess.scalar(select(func.count()).select_from(models.node))
    live = sess.scalar(select(func.count()).select_from(models.node)
                       .where(models.node.semantic_run_id.in_(current))) if current else 0
    ident = sess.scalar(select(func.count()).select_from(models.identity))
    members = sess.scalar(select(func.count()).select_from(models.member))
    live_members = sess.scalar(
        select(func.count()).select_from(models.member)
        .join(models.node, models.node.id == models.member.node_id)
        .where(models.node.semantic_run_id.in_(current))) if current else 0
    print(f"  --- {name} ---")
    print(f"    узлов всего / текущего поколения: {total} / {live}")
    print(f"    личностей / членств:              {ident} / {members}")
    print(f"    членств на текущее поколение:     {live_members}")
    return members, live_members


pub_m, pub_live = report("public", PUBLIC_MODELS, session)
hea_m, hea_live = 0, 0
if health_schema_configured():
    # `health_session()` требует тенанта явно: соединение отдельное и
    # само привязывается к владельцу (health_schema.py:63).
    tenant = bind_knowledge_user(session, None)
    with health_session(tenant) as hs:
        hea_m, hea_live = report("health", HEALTH_MODELS, hs)
else:
    print("  --- health --- схема не настроена")

print()
total_members, total_live = pub_m + hea_m, pub_live + hea_live
if total_members and total_live == 0:
    print("  ВЫВОД: ни одно членство не указывает на текущее поколение.")
    print("  Слой личностей относится к прежним ревизиям — структурный")
    print("  ответ пуст не из-за отсутствия данных, а из-за непересобранного R6.")
elif total_live:
    print(f"  ВЫВОД: живых членств {total_live} — поколение ни при чём,")
    print("  причину смотреть в skipped/considered выше.")
else:
    print("  ВЫВОД: личностей нет вовсе — R6 на этих данных не запускался.")

session.rollback()
PYEOF
rc=$?

# Код возврата Python обязан дойти до workflow. Без этой проверки
# «ГОТОВО» печаталось и после упавшей проверки, а прогон оставался
# зелёным: так прошёл прогон 354 с AttributeError, и так прошёл бы
# любой провалившийся гейт ниже. Найдено аудитом владельца 06.09.2026;
# сам workflow код возврата пробрасывает верно, глушил его скрипт.
if [ "$rc" -ne 0 ]; then
  echo "############ ПРОВАЛ (код $rc) ############"
  exit "$rc"
fi

echo "############ ГОТОВО ############"
