"""P8.5.2 async ingest worker (§14.5.1, §14.6).

Отдельный процесс/контейнер от `helm-core` — см. `parsers.py` docstring
для причины: Docling тяжёлый (~5.7GB зависимостей, торч + OCR-модели) и
при разборе скана/сложного PDF может дать заметный скачок RAM; этот
процесс не должен иметь возможность повлиять на живой API, отвечающий
на вебхуки MAX/Telegram.

Опрашивает `knowledge_ingest_jobs`, забирает PENDING-задачи по одной
(`FOR UPDATE SKIP LOCKED` — на случай если воркеров однажды станет
больше одного; сегодня деплоится ровно один), парсит через
`parsers.parse_file()`, индексирует чанки при успехе, помечает
`NEEDS_REVIEW` при провале quality gate (§14.6: «если Docling тоже
FAIL — source status NEEDS_REVIEW, не создавать уверенные knowledge
facts»).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ingest import split_chunks
from .parsers import parse_file
from ..models import KnowledgeChunk, KnowledgeIngestJob, KnowledgeIngestStatus, KnowledgeSource, KnowledgeStatus

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5


def claim_next_job(session: Session) -> KnowledgeIngestJob | None:
    """Взять один PENDING job, пометить RUNNING. Возвращает None, если
    очередь пуста — вызывающий код решает, ждать или выйти."""
    job = session.scalar(
        select(KnowledgeIngestJob)
        .where(KnowledgeIngestJob.status == KnowledgeIngestStatus.PENDING)
        .order_by(KnowledgeIngestJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = KnowledgeIngestStatus.RUNNING
    session.flush()
    return job


def process_job(session: Session, job: KnowledgeIngestJob) -> None:
    """Разобрать один job. Не коммитит — вызывающий код решает транзакцию."""
    source = session.get(KnowledgeSource, job.source_id)
    if source is None:
        job.status = KnowledgeIngestStatus.FAILED
        job.error = "source не найден"
        return

    try:
        result = parse_file(Path(source.raw_path))
    except Exception as exc:
        job.status = KnowledgeIngestStatus.FAILED
        job.error = f"{type(exc).__name__}: {exc}"
        logger.warning("knowledge ingest job %s failed: %s", job.id, job.error)
        return

    source.parser = result.parser

    if not result.quality_ok:
        source.status = KnowledgeStatus.NEEDS_REVIEW
        job.status = KnowledgeIngestStatus.NEEDS_REVIEW
        return

    # L1 SOURCE (§14.1): нормализованный Markdown — реальный файл, не
    # только запись в БД, чтобы Vault оставался открываемым обычным
    # Obsidian-совместимым клиентом (§14.2), не только через Postgres.
    Path(source.source_path).parent.mkdir(parents=True, exist_ok=True)
    Path(source.source_path).write_text(result.text, encoding="utf-8")

    for ordinal, chunk_text in enumerate(split_chunks(result.text)):
        session.add(KnowledgeChunk(
            source_id=source.id, ordinal=ordinal, text=chunk_text,
            tsv=func.to_tsvector("russian", chunk_text),
        ))
    job.status = KnowledgeIngestStatus.DONE


def run_forever(session_factory) -> None:  # pragma: no cover — процесс-луп
    logger.info("knowledge ingest worker started")
    while True:
        with session_factory() as session:
            job = claim_next_job(session)
            if job is None:
                session.commit()
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            process_job(session, job)
            session.commit()
            logger.info("knowledge ingest job %s -> %s", job.id, job.status)
