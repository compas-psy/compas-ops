"""L2 больше не зависит от того, вспомнит ли человек про backfill.

Разрыв, найденный аудитом владельца 06.09.2026: обычная загрузка файла
до семантического разбора НЕ ДОХОДИЛА. `worker.py::process_job` звал
`atomize_and_store()`, замороженный с R2 и возвращающий 0
(`atomizer.py:411`), а настоящий `publish_semantic_run()` вызывали три
ручных CLI. Корпус из 90 источников существовал ровно потому, что
backfill запускали руками.

Здесь проверяется проводка, а не качество разбора: связан ли путь
загрузки с очередью и не создаёт ли повтор второго задания. Качество
ревизии — предмет приёмки на живой модели, не юнит-теста.

Базы эти проверки не требуют: обе про структуру, а не про данные.
"""

from __future__ import annotations

import ast
import pathlib
import uuid

from sqlalchemy.dialects import postgresql

from helm_core.knowledge import worker as worker_mod
from helm_core.knowledge.semantic_jobs import enqueue_semantic
from helm_core.knowledge.semantic_publish import SEMANTIC_VERSION

_WORKER_SRC = pathlib.Path(worker_mod.__file__).read_text()


def _calls_in(func_name: str) -> set[str]:
    """Имена функций, реально вызываемых внутри `func_name`.

    Через AST, а не поиском подстроки: упоминание в комментарии или в
    докстроке — не вызов, и тест, который их путает, ловит не то.
    """
    tree = ast.parse(_WORKER_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return {
                child.func.id if isinstance(child.func, ast.Name) else child.func.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, (ast.Name, ast.Attribute))
            }
    raise AssertionError(f"в worker.py нет функции {func_name}")


def test_upload_enqueues_semantic_work():
    """Путь загрузки ставит задание на разбор."""
    assert "enqueue_semantic" in _calls_in("process_job")


def test_upload_no_longer_calls_the_frozen_atomizer():
    """И перестал звать замороженную заглушку.

    Она возвращает 0 при любом входе. Её вызов на месте L2 — это не
    «аддитивно и fail-open», а полное отсутствие слоя, замаскированное
    под работающий шаг.
    """
    assert "atomize_and_store" not in _calls_in("process_job")
    assert "from .atomizer import" not in _WORKER_SRC


def test_worker_loop_drains_the_semantic_queue():
    """Задание кто-то берёт. Очередь, в которую только кладут, — не
    очередь, а список сожалений."""
    assert "claim_next_semantic_job" in _calls_in("run_forever")
    assert "process_semantic_job" in _calls_in("run_forever")


def test_repeated_upload_of_identical_bytes_does_not_duplicate_work():
    """Ключ единственности — пользователь, источник, содержимое, версия.

    Проверяется скомпилированный SQL, а не намерение: `on_conflict_do_
    nothing` без указания constraint молча выродился бы в «конфликт по
    первичному ключу», то есть ни во что — id у нас всегда новый.
    """
    captured = {}

    class _Session:
        def execute(self, stmt):
            captured["sql"] = str(stmt.compile(dialect=postgresql.dialect()))

            class _R:
                def scalar_one_or_none(self):
                    return None
            return _R()

    enqueue_semantic(_Session(), source_id=uuid.uuid4(), knowledge_user_id=uuid.uuid4(),
                     source_sha256="a" * 64)
    sql = captured["sql"]
    assert "ON CONFLICT ON CONSTRAINT uq_knowledge_semantic_jobs_work DO NOTHING" in sql


def test_enqueued_version_matches_what_publication_writes():
    """Одно число на «что ставим в очередь» и «что публикуем».

    До 06.09.2026 их было два: константа жила в `backfill.py`, а у
    `publish_semantic_run()` стояло своё умолчание 2 — и пилот с
    приёмкой писали ревизии, которые порог 3 текущими не признаёт.
    """
    import inspect

    from helm_core.knowledge.semantic_publish import publish_semantic_run

    signature = inspect.signature(publish_semantic_run)
    assert signature.parameters["semantic_version"].default is inspect.Parameter.empty, (
        "версия снова получила умолчание — расхождение вернётся молча")
    assert SEMANTIC_VERSION >= 3
