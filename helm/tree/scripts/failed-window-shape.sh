#!/usr/bin/env bash
# HELM · на что похож фрагмент, который валит разбор.
#
# ЗАЧЕМ. Прогон 526: предел генерации сработал, окно 446 поделилось,
# узлов стало 2472 вместо 2453, покрытие 0.998. Но один кусок — 261
# символ, окно 473 — снова упёрся в предел и дальше не делится
# (`TRUNCATED_UNSPLITTABLE`, WINDOW_MIN_CHARS = 200).
#
# 261 символ на входе и больше 1600 токенов на выходе — это шесть
# токенов на символ. Извлечением так не бывает: модель зациклилась на
# самом тексте. Прошлый такой случай (R8_STUCK_SOURCES_2026-09-05) был
# плотной таблицей без абзацев и точек.
#
# ЧЕГО ЭТОТ СКРИПТ НЕ ДЕЛАЕТ. Не печатает сам текст. Это документ
# владельца, а логи прогона видны в GitHub; для решения хватает ФОРМЫ:
# сколько строк, какой длины, чего в нём больше — букв, цифр или знаков,
# есть ли разметка таблицы и насколько текст повторяется. Форма
# отличает таблицу от прозы, не разглашая ни того, ни другого.
set -uo pipefail
cd /opt/helm/compose || exit 1

SOURCE_NAME="${1:-MASTER_TZ.md}"

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - "$SOURCE_NAME" <<'PYEOF'
import collections
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.semantic_pilot import source_text
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models import (KnowledgeSemanticRun, KnowledgeSemanticWindow,
                              KnowledgeSource)

session = sessionmaker(bind=create_engine(get_settings().database_url,
                                          pool_pre_ping=True))()
bind_knowledge_user(session, None)
source = session.scalars(
    select(KnowledgeSource).where(KnowledgeSource.original_filename == sys.argv[1])
    .order_by(KnowledgeSource.created_at.desc()).limit(1)).one_or_none()
if source is None:
    print("  источника нет")
    raise SystemExit(0)

run = session.scalars(
    select(KnowledgeSemanticRun).where(KnowledgeSemanticRun.source_id == source.id)
    .order_by(KnowledgeSemanticRun.created_at.desc()).limit(1)).one_or_none()
failed = session.scalars(
    select(KnowledgeSemanticWindow)
    .where(KnowledgeSemanticWindow.semantic_run_id == run.id,
           KnowledgeSemanticWindow.status == "failed")
    .order_by(KnowledgeSemanticWindow.ordinal)).all() if run else []
if not failed:
    print("  провалившихся окон нет")
    raise SystemExit(0)

text = source_text(source)
if text is None:
    print("  текст источника недоступен")
    raise SystemExit(0)

for window in failed:
    piece = text[window.char_start:window.char_end]
    lines = piece.splitlines()
    kinds = collections.Counter(
        "буква" if ch.isalpha() else
        "цифра" if ch.isdigit() else
        "пробел" if ch.isspace() else "знак"
        for ch in piece)
    trigrams = collections.Counter(piece[i:i + 3] for i in range(len(piece) - 2))
    top, top_count = trigrams.most_common(1)[0] if trigrams else ("", 0)

    print(f"  окно {window.ordinal}: символы {window.char_start}–{window.char_end}"
          f" ({len(piece)}), код {window.error_code or '—'}")
    print(f"    строк: {len(lines)}; самая длинная: "
          f"{max((len(line) for line in lines), default=0)}; "
          f"пустых: {sum(1 for line in lines if not line.strip())}")
    print(f"    состав: " + ", ".join(f"{name} {count}" for name, count in kinds.most_common()))
    print(f"    конечная пунктуация (.!?): {sum(piece.count(ch) for ch in '.!?')}")
    print(f"    разметка таблицы (| и +): {piece.count('|') + piece.count('+')}")
    print(f"    подряд идущих одинаковых символов максимум: "
          f"{max((len(list(group)) for _, group in __import__('itertools').groupby(piece)), default=0)}")
    print(f"    самая частая тройка символов встречается {top_count} раз "
          f"из {max(len(piece) - 2, 0)} — доля {top_count / max(len(piece) - 2, 1):.2f}")
    print(f"    различных символов: {len(set(piece))}")
PYEOF
