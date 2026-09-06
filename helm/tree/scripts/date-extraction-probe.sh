#!/bin/bash
# HELM · дала ли модель даты после исправления контракта.
#
# action=recon: в БД не пишет ничего. Модель ВЫЗЫВАЕТСЯ — иначе проверять
# нечего; это тот же gemma2:2b, что и в backfill, отдельного состояния
# не заводится.
#
# Владелец 05.09.2026, пункт 3 порядка: «прогнать 5-8 реальных
# датонасыщенных окон, не весь корпус. Мне нужны всего четыре числа».
#
#   1. атомов всего;
#   2. сколько модель вернула с датой;
#   3. сколько дат прошло grounding;
#   4. сколько отклонено ИМЕННО date-grounding.
#
# Число 2 не наблюдается напрямую (атом с непрошедшей датой
# отбрасывается целиком), поэтому считается как сумма 3 и 4 плюс отказы
# гейта относительной даты. Это точно для атомов, не отброшенных по
# другой причине; там, где причина другая, дата модели не видна вовсе, и
# такие случаи считаются отдельной строкой, а не подмешиваются.
#
# Окна берутся не наугад: только те, где в тексте ЕСТЬ абсолютная дата
# по строгому регексу. Проверять извлечение дат на окне без дат
# бессмысленно.
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

sudo docker compose exec -T helm-core python3 - <<'PYEOF'
"""Извлечение на датонасыщенных окнах: четыре числа и примеры."""
import re
import time

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from helm_core.config import get_settings
from helm_core.knowledge.health_schema import health_schema_configured, health_session
from helm_core.knowledge.semantic_extract import extract_nodes_window
from helm_core.knowledge.semantic_pilot import source_text
from helm_core.knowledge.semantic_windows import build_windows
from helm_core.knowledge.tenancy import bind_knowledge_user
from helm_core.models.health_tables import HealthKnowledgeSourcePrivate
from helm_core.models.tables import KnowledgeSource

WINDOWS_WANTED = 6

#: Потолок на весь замер. Прогон 319 уложился в 14 минут, прогон 321 на
#: тех же шести окнах шёл больше двух часов — локальная модель на CPU
#: даёт такой разброс, а логи Actions недоступны, пока job не завершён,
#: так что «подожду ещё» вслепую стоит часов. Бюджет проверяется ПЕРЕД
#: окном: начатое доводится до конца, недосчитанное честно названо.
BUDGET_SECONDS = 45 * 60

#: Строгая дата: год словом, ДД.ММ.ГГГГ целиком, название месяца.
#: Лабораторное «5.4» и номер приказа сюда не попадают.
DATE_RE = re.compile(
    r"\b(19|20)\d{2}\b|\b\d{1,2}[./]\d{1,2}[./](19|20)\d{2}\b|"
    r"январ|феврал|март|апрел|\bма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр",
    re.IGNORECASE)

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
with sessionmaker(engine, expire_on_commit=False)() as session:
    tenant = bind_knowledge_user(session, None)
    if not health_schema_configured():
        print("health-схема не настроена — проверять нечего")
        raise SystemExit(1)

    picked = []
    with health_session(tenant) as graph:
        names = {
            row.source_id: row.original_filename
            for row in graph.execute(select(HealthKnowledgeSourcePrivate)).scalars().all()
        }

    rows = session.execute(
        select(KnowledgeSource).where(KnowledgeSource.knowledge_user_id == tenant)
    ).scalars().all()

    for source in rows:
        if len(picked) >= WINDOWS_WANTED:
            break
        text = source_text(source)
        if not text:
            continue
        for window in build_windows(text):
            if DATE_RE.search(window.text):
                picked.append((names.get(source.id) or str(source.id)[:8], window))
                break

    print(f"окон отобрано (в тексте есть абсолютная дата): {len(picked)}")
    print()

    atoms_total = passed_with_date = rejected_by_date = rejected_relative = 0
    rejected_other = rejected_unparsable = 0
    examples = []
    #: Сами отказы grounding, дословно. Прогон 319 показал 38 таких на 7
    #: принятых атомов, и по одним числам нельзя сказать, гейт ли слишком
    #: строг или модель приписывает атомам чужую дату. Ответ — в тексте
    #: отказа: там и заявленная дата, и цитата, которая её не содержит.
    unconfirmed = []

    began = time.monotonic()
    done = 0
    for number, (filename, window) in enumerate(picked, start=1):
        if time.monotonic() - began >= BUDGET_SECONDS:
            print(f"  бюджет {BUDGET_SECONDS // 60} минут исчерпан, "
                  f"окна {number}-{len(picked)} не считались", flush=True)
            break
        # Печать ПО ХОДУ, а не в конце. Первая редакция молчала до
        # последнего окна: прогон, идущий второй час, выглядел из лога
        # неотличимо от зависшего, и оборвать его означало потерять всё
        # уже посчитанное. Числа всё равно сводятся в конце.
        started = time.monotonic()
        print(f"  окно {number}/{len(picked)} ({len(window.text)} символов, "
              f"{filename}) — считаю…", flush=True)
        try:
            result = extract_nodes_window(window.text, domain="health")
        except Exception as exc:  # noqa: BLE001 — печатаем и идём дальше
            print(f"    извлечение упало за {time.monotonic() - started:.0f}с — "
                  f"{type(exc).__name__}: {exc}", flush=True)
            continue
        with_date = sum(1 for atom in result.atoms if atom.occurred_at)
        print(f"    {time.monotonic() - started:.0f}с, атомов {len(result.atoms)}, "
              f"из них с датой {with_date}, отклонено {len(result.rejected)}", flush=True)
        done += 1
        atoms_total += len(result.atoms)
        for atom in result.atoms:
            if atom.occurred_at:
                passed_with_date += 1
                if len(examples) < 5:
                    quote = " ".join((atom.evidence_quote or "").split())[:120]
                    examples.append((atom.title, atom.occurred_at, atom.date_precision,
                                     quote, filename))
        for note in result.rejected:
            if "не подтверждён" in note:
                rejected_by_date += 1
                if len(unconfirmed) < 12:
                    unconfirmed.append((note, filename))
            elif "относительную дату" in note:
                rejected_relative += 1
            elif "не в разбираемой форме" in note:
                rejected_unparsable += 1
            else:
                rejected_other += 1

    model_gave_date = passed_with_date + rejected_by_date + rejected_relative
    print(f"############ ЧЕТЫРЕ ЧИСЛА (окон посчитано: {done} из {len(picked)}) ############")
    print(f"  1. атомов всего (принято):                  {atoms_total}")
    print(f"  2. модель вернула с датой:                  {model_gave_date}")
    print(f"  3. дат прошло grounding:                    {passed_with_date}")
    print(f"  4. отклонено ИМЕННО date-grounding:         {rejected_by_date}")
    print(f"     (отдельно: отказ по относительной дате:  {rejected_relative})")
    print(f"     (отдельно: occurred_at не дата вообще:   {rejected_unparsable})")
    print(f"     (отдельно: отказы по другим причинам:    {rejected_other})")
    print()
    print("############ ПРИМЕРЫ: событие → дата → цитата ############")
    if not examples:
        print("  примеров нет: ни одна дата не прошла")
    for title, occurred, precision, quote, filename in examples:
        print(f"  {title} → {occurred} ({precision}) → «{quote}»")
        print(f"      источник: {filename}")
    print()
    print("############ ОТКАЗЫ GROUNDING ДОСЛОВНО ############")
    if not unconfirmed:
        print("  ни одного")
    for note, filename in unconfirmed:
        print(f"  {note}")
        print(f"      источник: {filename}")
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
