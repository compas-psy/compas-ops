#!/usr/bin/env bash
# HELM · какими словами корпус подписывает даты. Чтение.
#
# Замер потолка (date-anchor-ceiling.sh, прогон 355) дал 5.6%, но
# интересно в нём другое число: 186 якорей из 273 остались
# `unlabelled`, и 113 окон из 122 не содержат ни одного якоря события.
# Словарь подписей в `temporal.py` я составил по своему представлению о
# медицинском бланке, а не по корпусу. Прежде чем объявлять даты
# тупиком, надо увидеть настоящие формулировки.
#
# Печатается ХВОСТ текста перед неузнанной датой, нормализованный:
# последние три слова, в нижнем регистре, с вырезанными числами. Сам
# документ, имя файла и значения из него сюда не попадают — вопрос
# только про слова-подписи.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Словарь подписей дат по факту, а не по догадке. Ничего не пишет."""
import re
from collections import Counter

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.semantic_pilot import source_text
from helm_core.knowledge.semantic_publish import HEALTH_MODELS, PUBLIC_MODELS
from helm_core.knowledge.temporal import ROLE_UNLABELLED, find_date_anchors
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import KnowledgeSource
from helm_core.models.base import KnowledgeStatus

WINDOW = 48
tails = Counter()
last_word = Counter()
no_head = 0
total = 0

_DIGITS = re.compile(r"\d+")
_WS = re.compile(r"\s+")


def normalize(head: str) -> str:
    """Последние три слова перед датой, без чисел и знаков."""
    head = _DIGITS.sub("#", head)
    head = re.sub(r"[^\w#\s]", " ", head, flags=re.UNICODE)
    words = _WS.sub(" ", head).strip().lower().split()
    return " ".join(words[-3:])


def scan(models, graph, sources):
    global no_head, total
    for source in sources:
        text = source_text(source)
        if text is None:
            continue
        run_id = source.current_semantic_run_id
        windows = graph.execute(
            select(models.window.char_start, models.window.char_end)
            .where(models.window.semantic_run_id == run_id)).all()
        for start, end in windows:
            chunk = text[start:end]
            for anchor in find_date_anchors(chunk):
                if anchor.role != ROLE_UNLABELLED:
                    continue
                total += 1
                tail = normalize(chunk[max(0, anchor.char_start - WINDOW):anchor.char_start])
                if not tail:
                    no_head += 1
                    continue
                tails[tail] += 1
                last_word[tail.split()[-1]] += 1


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
session = sessionmaker(bind=engine)()
tenant = bind_knowledge_user(session, None)

sources = session.scalars(
    select(KnowledgeSource).where(KnowledgeSource.status == KnowledgeStatus.ACTIVE,
                                  KnowledgeSource.current_semantic_run_id.is_not(None))).all()
health = [s for s in sources if s.domain == "health"]
other = [s for s in sources if s.domain != "health"]

scan(PUBLIC_MODELS, session, other)
if health and health_schema_configured():
    with health_session(tenant) as graph:
        scan(HEALTH_MODELS, graph, health)

print("############ ПОДПИСИ НЕУЗНАННЫХ ДАТ ############")
print(f"  неузнанных якорей:      {total}")
print(f"  из них без текста слева: {no_head}")
print()
print("  ── слово вплотную перед датой, по частоте ──")
for word, count in last_word.most_common(40):
    print(f"    {count:4d}  {word}")
print()
print("  ── три слова перед датой, по частоте ──")
for tail, count in tails.most_common(40):
    print(f"    {count:4d}  {tail}")

session.rollback()
PYEOF

echo "############ ГОТОВО ############"
