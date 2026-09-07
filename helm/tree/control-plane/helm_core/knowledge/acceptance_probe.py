"""Замеры для приёмки порционной обработки. Только чтение и переразбор.

Отдельный модуль, а не heredoc в скрипте: те же три замера нужны и до
прогона, и во время, и после, а копия python-кода в трёх местах bash-
скрипта расходится на второй правке.

Ничего, кроме постановки источника на переразбор, здесь не меняется —
и это единственное изменение делается названной функцией
(`request_rederivation`), а не UPDATE'ом статуса.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from ..config import get_settings
from ..models import (
    KnowledgeAnswerRun, KnowledgeSemanticJob, KnowledgeSemanticRun,
    KnowledgeSemanticWindow, KnowledgeSource,
)
from .semantic_jobs import request_rederivation
from .semantic_publish import SEMANTIC_VERSION
from .tenancy import bind_knowledge_user


def _session():
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    session = sessionmaker(bind=engine)()
    bind_knowledge_user(session, None)
    return session


def _largest_source(session) -> KnowledgeSource | None:
    """Самый крупный источник корпуса — он и есть «книга» приёмки."""
    from ..models import KnowledgeChunk

    row = session.execute(
        select(KnowledgeChunk.source_id, func.count().label("n"))
        .group_by(KnowledgeChunk.source_id)
        .order_by(func.count().desc()).limit(1)).first()
    return session.get(KnowledgeSource, row[0]) if row else None


def state() -> None:
    session = _session()
    source = _largest_source(session)
    if source is None:
        print("  корпус пуст")
        return
    from ..models import KnowledgeChunk

    chunks = session.scalar(select(func.count()).select_from(KnowledgeChunk)
                            .where(KnowledgeChunk.source_id == source.id))
    print(f"  крупнейший источник: {source.original_filename or source.raw_path}")
    print(f"    чанков L1: {chunks}")
    print(f"    текущая ревизия: {source.current_semantic_run_id}")

    job = session.scalar(select(KnowledgeSemanticJob).where(
        KnowledgeSemanticJob.source_id == source.id,
        KnowledgeSemanticJob.semantic_version == SEMANTIC_VERSION))
    if job is None:
        print("    задания нет")
    else:
        print(f"    задание: {job.status} попыток={job.attempts} "
              f"аренда={job.lease_expires_at} ошибка={job.error or '—'}")

    run = session.scalars(
        select(KnowledgeSemanticRun)
        .where(KnowledgeSemanticRun.source_id == source.id,
               KnowledgeSemanticRun.semantic_version == SEMANTIC_VERSION)
        .order_by(KnowledgeSemanticRun.created_at.desc()).limit(1)).first()
    if run is None:
        print("    ревизии нет")
        return
    top = session.scalar(
        select(func.count()).select_from(KnowledgeSemanticWindow)
        .where(KnowledgeSemanticWindow.semantic_run_id == run.id,
               KnowledgeSemanticWindow.parent_window_id.is_(None))) or 0
    print(f"    ревизия {run.status}: окон верхнего уровня разобрано {top}, "
          f"всего окон {run.windows_total}, провалено {run.windows_failed}, "
          f"узлов {run.nodes_created}, рёбер {run.edges_created}, "
          f"покрытие {run.coverage_ratio}")


def rederive() -> None:
    session = _session()
    source = _largest_source(session)
    if source is None:
        print("  корпус пуст, переразбирать нечего")
        return
    done = request_rederivation(session, source_id=source.id,
                                semantic_version=SEMANTIC_VERSION)
    session.commit()
    print(f"  переразбор {source.original_filename or source.raw_path}: "
          f"{'поставлен' if done else 'задания на эту версию нет'}")


def paid() -> None:
    """Сколько ответов за последний час были платными. Ноль — цель."""
    session = _session()
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = session.execute(
        select(KnowledgeAnswerRun.mode, KnowledgeAnswerRun.paid_ai_used, func.count())
        .where(KnowledgeAnswerRun.created_at >= since)
        .group_by(KnowledgeAnswerRun.mode, KnowledgeAnswerRun.paid_ai_used)).all()
    if not rows:
        print("  ответов за час не записано")
    for mode, used, count in rows:
        print(f"  режим {mode}: платных={used} штук {count}")
    total_paid = sum(c for _, used, c in rows if used)
    print(f"  ИТОГО платных вызовов за час: {total_paid}")


if __name__ == "__main__":
    {"state": state, "rederive": rederive, "paid": paid}[sys.argv[1]]()
