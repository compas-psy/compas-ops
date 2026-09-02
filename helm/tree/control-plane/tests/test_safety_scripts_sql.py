"""Регрессия на класс дефекта, найденный владельцем 02.09.2026 (прогон #152).

Тест восстановления спрашивал `stored_path` и `sha256` у
`health.knowledge_source_private` — колонок, которых там нет. Запрос падал,
но стоял внутри подстановки процессов `< <(...)`, где код возврата основной
оболочке не виден. Цикл получил ноль строк, счётчик пропаж остался нулём,
и скрипт напечатал «восстановлено 90 из 90» и `RESTORE TEST PASSED`.

Дефект был не в том, что ошиблись именем колонки, — ошибиться можно всегда.
Дефект в том, что ошибка превратилась в зелёную проверку. Поэтому тестов
здесь три, и каждый закрывает своё звено:

1. имена колонок в SQL внутри shell-скриптов сверяются с моделями;
2. вывод SQL не читается через подстановку процессов;
3. отметка `last-restore-test` ставится последней строкой — после всех
   проверок, а не в середине.

Тесты читают скрипты как текст. Это слабее живого прогона, но живой прогон
требует restic, docker и удалённого хранилища, а эти проверки должны идти
на каждом коммите.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

#: Скрипты, которые решают, можно ли делать необратимое. Ошибка здесь стоит
#: дороже, чем где-либо ещё в репозитории.
SAFETY_SCRIPTS = [
    SCRIPTS / "restore_test.sh",
    SCRIPTS / "destructive-drop-public-health-chunks.sh",
    SCRIPTS / "r1-verify.sh",
]


def _code(script: Path) -> str:
    """Скрипт без комментариев.

    Проверять надо исполняемые строки. Комментарий, объясняющий дефект,
    обязан называть его своими словами — иначе разбор придётся писать
    иносказаниями, и он перестанет быть разбором.
    """
    lines = []
    for line in script.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        lines.append(line.split(" #", 1)[0] if " #" in line else line)
    return "\n".join(lines)


def _known_columns() -> dict[str, set[str]]:
    from helm_core.models.health_tables import HealthKnowledgeSourcePrivate
    from helm_core.models.tables import KnowledgeSource

    return {
        "health.knowledge_source_private": {
            c.name for c in HealthKnowledgeSourcePrivate.__table__.columns
        },
        "knowledge_sources": {c.name for c in KnowledgeSource.__table__.columns},
    }


@pytest.mark.parametrize("script", SAFETY_SCRIPTS, ids=lambda p: p.name)
def test_sql_referencing_sidecar_uses_columns_that_exist(script: Path) -> None:
    """Колонка, которой нет в модели, не должна попасть в SQL страховочного
    скрипта. Именно эта ошибка дала ложный PASS в прогоне #152."""
    text = _code(script)
    columns = _known_columns()["health.knowledge_source_private"]

    # Обращения `<алиас>.<колонка>` рядом с упоминанием сайдкара. Окно, а
    # не весь файл: один и тот же короткий алиас (`p`, `h`, `s`) в разных
    # запросах означает разные таблицы, и проверка «по всему тексту» ловила
    # бы CTE из соседнего запроса. Настоящая ошибка — та, что рядом с FROM.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for match in re.finditer(
            r"health\.knowledge_source_private\s+(\w+)\b", line
        ):
            alias = match.group(1)
            if alias.lower() in {"as", "where", "group", "order", "on", "join"}:
                continue
            window = "\n".join(lines[max(0, i - 2):i + 4])
            used = set(re.findall(rf"\b{re.escape(alias)}\.(\w+)", window))
            unknown = used - columns
            assert not unknown, (
                f"{script.name}:{i + 1} обращается к "
                f"health.knowledge_source_private.{sorted(unknown)}, "
                f"а в модели только {sorted(columns)}"
            )

    # Отдельно: сами имена ушедших колонок не должны встречаться рядом с
    # сайдкаром ни в каком виде. Путь и хэш живут в knowledge_sources.
    if "knowledge_source_private" in text:
        for gone in ("stored_path",):
            assert gone not in text, (
                f"{script.name} упоминает {gone!r}: такой колонки нет ни в "
                "сайдкаре, ни в конверте — путь это raw_path/source_path"
            )


@pytest.mark.parametrize("script", SAFETY_SCRIPTS, ids=lambda p: p.name)
def test_sql_output_is_not_read_through_process_substitution(script: Path) -> None:
    """`done < <(psql ...)` теряет код возврата запроса.

    Внутри подстановки процессов `set -e` не действует и статус команды
    основной оболочке не виден: упавший запрос выглядит как пустая выборка.
    Для проверки, решающей судьбу данных, это недопустимо — читать надо из
    файла, записанного с проверкой кода возврата.
    """
    text = _code(script)
    offenders = [
        line.strip()
        for line in text.splitlines()
        if "< <(" in line
    ]
    assert not offenders, (
        f"{script.name} читает вывод команды через подстановку процессов: "
        f"{offenders}. Код возврата там теряется — пишите в файл и "
        "проверяйте статус."
    )


def test_restore_test_marks_success_only_after_every_check() -> None:
    """`touch last-restore-test` обязан быть последним действием.

    Отметка — это то, что читает гейт необратимых операций. Поставленная
    раньше проверок, она означает «бэкап здоров» до того, как это доказано.
    """
    text = _code(SCRIPTS / "restore_test.sh")
    touch_at = text.index("touch /var/lib/helm-guardian/last-restore-test")
    for later in ("FAIL:", "exit 1"):
        assert text.rindex(later) < touch_at, (
            "после отметки last-restore-test остались проверки, способные "
            f"упасть ({later!r}) — отметка ставится до доказательства"
        )


def test_failing_query_inside_process_substitution_is_silent() -> None:
    """Доказательство самого свойства, а не его отсутствия в наших файлах.

    Тест существует, чтобы объяснение в комментариях не разошлось с
    поведением bash: слева — форма, которая молчит об ошибке, справа —
    форма, которая падает. Если однажды bash изменит поведение, тест
    заметит это раньше, чем очередная страховка соврёт.
    """
    silent = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         'n=0; while read -r _; do n=$((n+1)); done < <(false); echo "$n"'],
        capture_output=True, text=True,
    )
    assert silent.returncode == 0, "подстановка процессов вдруг стала фатальной"
    assert silent.stdout.strip() == "0"

    loud = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         'false > /dev/null || exit 1; echo "не должно напечататься"'],
        capture_output=True, text=True,
    )
    assert loud.returncode == 1
    assert loud.stdout.strip() == ""
