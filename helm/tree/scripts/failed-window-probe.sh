#!/usr/bin/env bash
# HELM · поможет ли деление фрагмента, который валит разбор.
#
# ЗАЧЕМ. Прогон 528 показал форму окна 473: одна строка, 261 символ, ни
# одной конечной точки. `split_window()` отказывается его делить ДО
# всякой попытки — `len(text) < WINDOW_MIN_CHARS * 2`, то есть 261 < 400.
# Порог заведён под переполнение атомами, где половинки вышли бы
# бессмысленно мелкими; к зацикливанию модели он отношения не имеет.
#
# Прежде чем снимать порог в продакшне, надо знать: РАЗБИРАЕТСЯ ли
# половина. Если модель зацикливается и на 130 символах — правка не
# поможет, и честнее это узнать за одну попытку, чем выкатывать код,
# который ничего не чинит.
#
# ЧТО ЭТО ДЕЛАЕТ. Зовёт `extract_nodes_window()` на самом фрагменте и на
# его половинах, поделённых по границе слова. Ничего не пишет: сессии
# нет, БД не трогается, результат только в лог. Текст фрагмента не
# печатается — печатается исход и время.
set -uo pipefail
cd /opt/helm/compose || exit 1

SOURCE_NAME="${1:-MASTER_TZ.md}"

echo "############ ВЫКАЧЕНО ############"
echo "SHA: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - "$SOURCE_NAME" <<'PYEOF'
import sys
import time

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.semantic_extract import (MAX_GENERATION_TOKENS, ExtractionFailed,
                                                  WindowTruncated, extract_nodes_window)
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
run = session.scalars(
    select(KnowledgeSemanticRun).where(KnowledgeSemanticRun.source_id == source.id)
    .order_by(KnowledgeSemanticRun.created_at.desc()).limit(1)).one_or_none()
window = session.scalars(
    select(KnowledgeSemanticWindow)
    .where(KnowledgeSemanticWindow.semantic_run_id == run.id,
           KnowledgeSemanticWindow.status == "failed")
    .order_by(KnowledgeSemanticWindow.ordinal).limit(1)).one_or_none()
if window is None:
    print("  провалившихся окон нет — пробовать нечего")
    raise SystemExit(0)

text = source_text(source)
piece = text[window.char_start:window.char_end]
session.rollback()

print(f"  предел генерации: {MAX_GENERATION_TOKENS} токенов")
print(f"  фрагмент: окно {window.ordinal}, {len(piece)} символов")


def word_halves(value: str) -> list[str]:
    """Пополам по ближайшему пробелу — слово рвать нельзя."""
    middle = value.rfind(" ", 0, len(value) // 2 + 20)
    if middle <= 0:
        return [value]
    return [value[:middle].strip(), value[middle:].strip()]


def attempt(label: str, value: str) -> bool:
    started = time.monotonic()
    try:
        result = extract_nodes_window(value, domain=source.domain)
    except WindowTruncated as exc:
        print(f"  {label} ({len(value)} симв): УПЁРЛОСЬ В ПРЕДЕЛ — {exc}"
              f"  [{time.monotonic() - started:.0f} с]")
        return False
    except ExtractionFailed as exc:
        print(f"  {label} ({len(value)} симв): ОТКАЗ — {exc}"
              f"  [{time.monotonic() - started:.0f} с]")
        return False
    print(f"  {label} ({len(value)} симв): РАЗОБРАЛОСЬ — "
          f"сущностей {len(result.entities)}, атомов {len(result.atoms)}"
          f"  [{time.monotonic() - started:.0f} с]")
    return True


print()
print("############ ЦЕЛИКОМ ############")
attempt("фрагмент целиком", piece)

print()
print("############ ПОЛОВИНЫ ############")
halves = word_halves(piece)
if len(halves) < 2:
    print("  поделить по слову не вышло")
    raise SystemExit(0)
survived = [attempt(f"половина {index + 1}", half) for index, half in enumerate(halves)]

if all(survived):
    print()
    print("  ВЫВОД: деление помогает — обе половины разбираются.")
    raise SystemExit(0)

print()
print("############ ЧЕТВЕРТИ ############")
for index, half in enumerate(halves):
    if survived[index]:
        continue
    for part_index, part in enumerate(word_halves(half)):
        attempt(f"четверть {index + 1}.{part_index + 1}", part)
PYEOF
