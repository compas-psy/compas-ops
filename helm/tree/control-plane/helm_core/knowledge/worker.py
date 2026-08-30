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
from ..outbox import enqueue

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
    """Разобрать один job. Не коммитит — вызывающий код решает транзакцию.

    НАЙДЕНО на живом смоук-тесте 29.08.2026: раньше try/except оборачивал
    только parse_file() — исключение из ЛЮБОГО шага после него (запись
    L1 SOURCE на диск, создание chunks) улетало необработанным из
    process_job() прямо в run_forever(), валило весь процесс, транзакция
    откатывалась (job возвращался в PENDING), Docker
    (restart: unless-stopped) поднимал контейнер заново — и тот тут же
    падал на ТОЙ ЖЕ задаче. Один плохой job уводил воркер в вечный
    краш-луп вместо того, чтобы просто получить FAILED и уступить место
    следующему. Один try/except на всё тело — единственный правильный
    контракт для функции, которую вызывающий код обязан не крашить.
    """
    source = session.get(KnowledgeSource, job.source_id)
    if source is None:
        job.status = KnowledgeIngestStatus.FAILED
        job.error = "source не найден"
        return

    chunk_count = 0
    try:
        result = parse_file(Path(source.raw_path))
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
            chunk_count += 1
        job.status = KnowledgeIngestStatus.DONE
    except Exception as exc:
        job.status = KnowledgeIngestStatus.FAILED
        job.error = f"{type(exc).__name__}: {exc}"
        logger.warning("knowledge ingest job %s failed: %s", job.id, job.error)
    finally:
        # P8.5.7, "3 шага": получен -> сохранён/разбор запущен -> разбор
        # завершён. finally — уведомление уходит независимо от того, каким
        # путём process_job() вышел (return из quality-gate-провала,
        # обычный конец при DONE, или except при исключении).
        _notify_owner_of_result(session, job, source, chunk_count)


def _notify_owner_of_result(session: Session, job: KnowledgeIngestJob,
                            source: KnowledgeSource, chunk_count: int) -> None:
    """Третий шаг диалога вложений (P8.5.7) — только для job'ов, заведённых
    из чата (`channel`+`recipient` заполнены `chat_intake.py`). `ingest_
    text()`/тестовые пути их не задают — уведомлять там некого, тихий
    no-op. Провал постановки в очередь не должен портить уже посчитанный
    результат разбора — это уведомление, не часть основной гарантии job'а."""
    if not job.channel or not job.recipient:
        return
    filename = source.original_filename or "без имени"
    if job.status == KnowledgeIngestStatus.DONE:
        text = f"Разбор «{filename}» завершён — сохранено фрагментов: {chunk_count}."
    elif job.status == KnowledgeIngestStatus.NEEDS_REVIEW:
        text = (f"Разбор «{filename}» не удался — не получилось надёжно "
               "извлечь текст. Файл сохранён, но не добавлен в базу знаний как факт.")
    else:
        text = f"Разбор «{filename}» завершился ошибкой — попробуйте прислать файл ещё раз."
    try:
        enqueue(session, channel=job.channel, recipient=job.recipient,
               reference=f"ingest-result:{job.id}", payload_reference={"text": text})
    except Exception:
        logger.exception("knowledge ingest job %s: не удалось поставить уведомление в очередь",
                         job.id)


def run_forever(session_factory) -> None:  # pragma: no cover — процесс-луп
    """Внешний try/except — на случай сбоя ВНЕ process_job (например,
    обрыв соединения с БД): один плохой цикл не должен ронять процесс и
    уводить контейнер в краш-луп, симметрично тому же уроку, что и
    process_job() выше — просто на уровень выше."""
    logger.info("knowledge ingest worker started")
    while True:
        try:
            with session_factory() as session:
                job = claim_next_job(session)
                if job is None:
                    session.commit()
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue
                process_job(session, job)
                session.commit()
                logger.info("knowledge ingest job %s -> %s", job.id, job.status)
        except Exception:
            logger.exception("knowledge ingest worker: необработанная ошибка цикла")
            time.sleep(POLL_INTERVAL_SECONDS)
