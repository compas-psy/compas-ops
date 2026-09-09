"""Производная ревизия обязана замечать изменение парсера и чанкинга.

ДЕФЕКТ, КОТОРЫЙ ЭТО СТЕРЕЖЁТ (07.09.2026). Ключ работы в очереди —
«пользователь, источник, содержимое, версия», где содержимое это sha256
файла. Значит те же байты после починки парсера попадают в тот же ключ,
и очередь считает работу выполненной — прежним, сломанным способом.
Так и вышло с fb2: книга лежала одним куском XML-разметки, а переразбор
пришлось запускать правкой статуса задания руками.

Проверка простая и грубая: отпечаток кода, который делает из байтов
вход для извлечения, обязан совпадать с записанным для ОБЪЯВЛЕННОЙ
версии. Меняешь парсер — тест краснеет, пока версия не поднята.
"""

from __future__ import annotations

import ast
import pathlib

from helm_core.knowledge.derivation import (
    DERIVATION_REVISION, DERIVING_MODULES, RECORDED_FINGERPRINTS,
    derivation_fingerprint,
)


def test_declared_version_matches_the_deriving_code():
    """Отпечаток нынешнего кода обязан быть записан за нынешней ревизией.

    КРАСНЫЙ ТЕСТ ЧИНИТСЯ НЕ ЗДЕСЬ. Если он покраснел — изменился один из
    `DERIVING_MODULES`, то есть те же байты файла теперь дадут другой
    вход для извлечения. Правильное действие: поднять
    `DERIVATION_REVISION` и ДОБАВИТЬ строку в `RECORDED_FINGERPRINTS`.
    Переписать существующую строку — значит объявить, что корпус,
    разобранный прежним способом, разобран нынешним.

    СЧЁТЧИК СВОЙ, А НЕ `SEMANTIC_VERSION`, И ЭТО НЕ КОСМЕТИКА (09.09.2026).
    Реестр ключевался версией извлечения, и тест требовал поднимать её
    на каждую правку парсера. Но версией ключуется семантическое
    ЗАДАНИЕ: её подъём ставит в очередь пересчёт всего корпуса, включая
    книгу на пятнадцать часов, — а до разбора файла не доходит вовсе.
    Переразбор ведёт отпечаток, и заново поднимаются ровно те источники,
    чей текст стал другим.
    """
    assert DERIVATION_REVISION in RECORDED_FINGERPRINTS, (
        f"ревизия {DERIVATION_REVISION} объявлена, но её отпечаток не записан")
    assert RECORDED_FINGERPRINTS[DERIVATION_REVISION] == derivation_fingerprint(), (
        "код разбора изменился без подъёма производной ревизии: те же байты "
        "дадут другую производную, а записано это нигде не будет")


def test_a_change_in_the_parser_moves_the_fingerprint():
    """Проверка самой проверки: отпечаток обязан реагировать на поведение."""
    before = derivation_fingerprint()
    module = pathlib.Path(__file__).resolve().parents[1] / "helm_core/knowledge/parsers.py"
    original = module.read_text(encoding="utf-8")
    try:
        module.write_text(original + "\n\ndef _probe_change():\n    return 1\n",
                          encoding="utf-8")
        assert derivation_fingerprint() != before, (
            "новая функция в парсере не сдвинула отпечаток — проверка слепая")
    finally:
        module.write_text(original, encoding="utf-8")
    assert derivation_fingerprint() == before


def test_a_comment_does_not_move_the_fingerprint():
    """Комментарий не меняет производную — и не должен стоить корпусу пересборки."""
    before = derivation_fingerprint()
    module = pathlib.Path(__file__).resolve().parents[1] / "helm_core/knowledge/chunking.py"
    original = module.read_text(encoding="utf-8")
    try:
        module.write_text(original + "\n# заметка, ничего не меняющая\n", encoding="utf-8")
        assert derivation_fingerprint() == before, (
            "комментарий сдвинул отпечаток — каждая правка текста требовала бы "
            "пересборки всего корпуса")
    finally:
        module.write_text(original, encoding="utf-8")


def test_every_deriving_module_exists_and_parses():
    here = pathlib.Path(__file__).resolve().parents[1] / "helm_core/knowledge"
    for name in DERIVING_MODULES:
        ast.parse((here / name).read_text(encoding="utf-8"))
