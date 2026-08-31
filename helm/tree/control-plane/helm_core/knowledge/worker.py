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

from .batch_intake import finalize_batch_if_terminal, sync_item_from_job
from .embeddings import embed_texts_or_none
from .ingest import split_chunks
from .parsers import parse_file
from .tenancy import bind_knowledge_user
from ..models import (
    KnowledgeBatchItem, KnowledgeChunk, KnowledgeIngestJob, KnowledgeIngestStatus,
    KnowledgeSource, KnowledgeStatus, KnowledgeUser, KnowledgeUserStatus,
)
from ..outbox import enqueue

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5

#: v3.8 §14.4 fair queue: индекс последнего обслуженного тенанта внутри
#: ЭТОГО процесса. Модульное состояние, не персистентное — справедливость
#: нужна на время жизни процесса воркера, не через рестарт (после
#: рестарта распределение снова стартует с начала списка, это не портит
#: гарантию "ни один тенант не голодает бесконечно").
_last_served_tenant_index = -1


def _active_tenant_ids(session: Session) -> list:
    """`knowledge_users` — не tenant-scoped, RLS на неё не распространяется
    (реестр тенантов, не их контент) — этот запрос видит всех, никакой
    привязки GUC для него не нужно."""
    return list(session.scalars(
        select(KnowledgeUser.id).where(KnowledgeUser.status == KnowledgeUserStatus.ACTIVE)
        .order_by(KnowledgeUser.created_at)
    ).all())


def claim_next_job(session: Session) -> KnowledgeIngestJob | None:
    """Взять один PENDING job, пометить RUNNING. Возвращает None, если
    очередь пуста у ВСЕХ тенантов — вызывающий код решает, ждать или выйти.

    v3.8 §14.4 fair queue: round-robin по тенантам, не глобальный FIFO —
    "one user's large ZIP does not starve another user's short upload".
    `knowledge_ingest_jobs` под RLS — единственный способ увидеть работу
    больше чем одного тенанта БЕЗ отдельной service-роли с BYPASSRLS
    (спека это разрешает, но заводить вторую БД-роль/секрет ради этого —
    инфраструктура без надобности, CLAUDE.md §2) — перебрать активных
    тенантов по одному, привязывая GUC к каждому по очереди, и взять
    первый, у которого вообще есть PENDING работа. Для сегодняшнего
    единственного тенанта (SYSTEM_OWNER) поведение не отличается от
    прежнего чистого FIFO — цикл на первой же итерации находит его job.
    """
    global _last_served_tenant_index
    tenants = _active_tenant_ids(session)
    if not tenants:
        return None
    n = len(tenants)
    for offset in range(n):
        idx = (_last_served_tenant_index + 1 + offset) % n
        tenant_id = tenants[idx]
        bind_knowledge_user(session, tenant_id)
        job = session.scalar(
            select(KnowledgeIngestJob)
            .where(KnowledgeIngestJob.knowledge_user_id == tenant_id,
                  KnowledgeIngestJob.status == KnowledgeIngestStatus.PENDING)
            .order_by(KnowledgeIngestJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is not None:
            _last_served_tenant_index = idx
            job.status = KnowledgeIngestStatus.RUNNING
            session.flush()
            return job
    return None


def _frontmatter(source: KnowledgeSource) -> str:
    """§14.3 markdown contract — обязательный YAML-блок для каждой
    normalized note. Собирается вручную, не через PyYAML: все значения —
    UUID/enum-строки/ISO-таймстемпы/hex-хэш, никогда свободный текст
    документа, экранирование не нужно, а зависимость не добавляется в
    Dockerfile.worker ради тривиального формата.

    `confidence`/`supersedes`/`contradicts` спека резервирует под derived/
    L2 note (`KnowledgeNote`, ещё не реализован, P8.5.6+) — L1 SOURCE
    (`type: source`) всегда `primary`/`extracted`, никогда `inferred`, и
    ничего не supersedes; не заполняются пустыми значениями, а не пишутся
    вовсе.
    """
    return "\n".join([
        "---",
        f"id: {source.id}",
        "type: source",
        f"domain: {source.domain}",
        f"created_at: {source.created_at.isoformat()}",
        f"updated_at: {source.updated_at.isoformat()}",
        f'source_ids: ["{source.id}"]',
        f'source_sha256: ["{source.sha256}"]',
        f"sensitivity: {source.sensitivity}",
        f"trust: {source.trust}",
        f"status: {source.status}",
        "---",
        "",
        "",
    ])


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

    v3.8 Фаза 1: привязка RLS-сессии — из immutable `job.knowledge_user_id`
    самого job'а, не повторное разрешение SYSTEM_OWNER (спека явно
    разрешает воркеру опираться на уже проставленный тенант durable job'а,
    §14.4 "Background workers... when a durable job already contains
    immutable knowledge_user_id") — это гарантирует, что даже когда
    claim_next_job() перестанет быть SYSTEM_OWNER-only (P8.6.4), разбор
    job'а физически не сможет тронуть чужой source.
    """
    tenant_id = bind_knowledge_user(session, job.knowledge_user_id)

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
        # НАЙДЕНО аудитом 29.08.2026: раньше писался голый текст, без
        # frontmatter — §14.3 markdown contract требует его для КАЖДОЙ
        # normalized note; без него domain/sensitivity/status файла видны
        # только через Postgres, а Vault, открытый напрямую (Obsidian,
        # SFTP), показывает неотличимые друг от друга .md-файлы — включая
        # health/client_restricted содержимое без единой видимой пометки.
        Path(source.source_path).parent.mkdir(parents=True, exist_ok=True)
        Path(source.source_path).write_text(_frontmatter(source) + result.text, encoding="utf-8")

        chunks = split_chunks(result.text)
        # ADR-025: та же fail-open политика, что ingest_text() — сбой
        # embed-сервиса не должен превращать job в FAILED, чанк просто
        # остаётся без embedding до бэкафилла.
        embeddings = embed_texts_or_none(chunks)
        for ordinal, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            session.add(KnowledgeChunk(
                knowledge_user_id=tenant_id, source_id=source.id, ordinal=ordinal,
                text=chunk_text,
                tsv=func.to_tsvector("russian", chunk_text),
                embedding=embedding,
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
        # v3.7 ZIP batch ingest: job, заведённый expand_batch(), не несёт
        # channel/recipient (§14.5.2 "no per-file push spam" — за него
        # уже отвечает _notify_owner_of_result() выше, тихо no-op) — но
        # batch-item должен узнать результат независимо, чтобы batch мог
        # когда-нибудь стать terminal и отправить ОДНО финальное
        # уведомление. Тот же finally, тот же контракт "не крашить
        # process_job()", что и для обычного уведомления.
        if job.batch_item_id is not None:
            item = session.get(KnowledgeBatchItem, job.batch_item_id)
            if item is not None:
                sync_item_from_job(session, item, job_status=job.status,
                                   chunk_count=chunk_count, job_error=job.error)
                finalize_batch_if_terminal(session, item.batch_id)


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
