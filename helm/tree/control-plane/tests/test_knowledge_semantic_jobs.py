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


# ── восстановление после падения воркера ─────────────────────────────
#
# Дыра из аудита владельца 06.09.2026: `claim` брал только PENDING, а
# RUNNING фиксировался коммитом ДО разбора (worker.py: «RUNNING виден
# снаружи на время разбора»). Воркер, убитый между этим коммитом и
# концом разбора, оставлял задание в RUNNING навсегда — его не видел ни
# один следующий воркер, и работа возвращалась только руками.
#
# Живая проверка (остановить воркер, поднять, получить завершённый
# разбор) — приёмка `p2-queue-recovery.sh`. Здесь проверяется форма
# запроса: без неё живая проверка сказала бы «сработало» и не сказала
# бы, почему.

class _CapturingSession:
    """Ловит выражение, не выполняя его. Базы в тестах нет."""

    def __init__(self):
        self.statements = []

    def scalar(self, stmt):
        self.statements.append(stmt)
        return None

    def scalars(self, stmt):
        self.statements.append(stmt)

        class _Empty:
            def all(self_inner):
                return []
        return _Empty()

    def flush(self):
        pass

    def sql(self, index=0):
        return str(self.statements[index].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True}))


def test_claim_takes_abandoned_jobs_not_only_new_ones():
    """Претендуемо и PENDING, и RUNNING с истёкшей арендой."""
    from helm_core.knowledge.semantic_jobs import claim_next_semantic_job

    session = _CapturingSession()
    claim_next_semantic_job(session)
    sql = session.sql()

    assert "'pending'" in sql, "новые задания больше не берутся"
    assert "'running'" in sql, "брошенное задание снова невидимо — дыра вернулась"
    assert "lease_expires_at" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql, (
        "без этого два воркера возьмут одно задание одновременно")


def test_claim_respects_the_attempt_limit():
    """Задание, которое роняет воркер каждый раз, не крутится вечно."""
    from helm_core.knowledge.semantic_jobs import MAX_ATTEMPTS, claim_next_semantic_job

    session = _CapturingSession()
    claim_next_semantic_job(session)
    assert f"attempts < {MAX_ATTEMPTS}" in session.sql()


def test_exhausted_jobs_are_closed_and_not_left_running():
    """«RUNNING навсегда» — то же самое молчание, только под другим
    именем: в отчёте по очереди оно читается как «работа идёт»."""
    from helm_core.knowledge.semantic_jobs import (MAX_ATTEMPTS,
                                                   fail_exhausted_semantic_jobs)

    session = _CapturingSession()
    fail_exhausted_semantic_jobs(session)
    sql = session.sql()
    assert "'running'" in sql
    assert f"attempts >= {MAX_ATTEMPTS}" in sql
    assert "lease_expires_at" in sql


def test_lease_is_longer_than_observed_parse_time():
    """Аренда короче разбора означала бы, что два воркера разбирают один
    источник — хуже, чем поздний возврат упавшего задания. Наблюдаемый
    разбор — минуты (приёмка P2: сорок секунд на средний документ)."""
    from helm_core.knowledge.semantic_jobs import LEASE_SECONDS

    assert LEASE_SECONDS >= 10 * 60


def test_worker_closes_exhausted_jobs_before_taking_the_next():
    assert "fail_exhausted_semantic_jobs" in _calls_in("run_forever")


def test_completed_job_releases_its_lease():
    """Оставленный срок у завершённого задания читается как «кто-то ещё
    работает». Проверяется по исходнику: у DONE-ветки обязан быть сброс."""
    import inspect

    from helm_core.knowledge.semantic_jobs import process_semantic_job

    source = inspect.getsource(process_semantic_job)
    assert source.count("lease_expires_at = None") >= 4, (
        "не все ветки завершения снимают аренду")
