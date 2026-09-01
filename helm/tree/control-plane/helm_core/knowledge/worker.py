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

ADR-021 фаза 2b: тот же процесс опрашивает и `knowledge_pending_
attachments` с `kind == "voice"` без транскрипта — после document-job'ов,
отдельной round-robin очередью (`claim_next_voice_pending()`/
`process_voice_pending()`), решая Remember-или-документ уже после
фоновой транскрипции.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .atomizer import atomize_and_store
from .audio import strip_timestamps, transcribe_audio
from .batch_intake import finalize_batch_if_terminal, sync_item_from_job
from .chat_intake import voice_ready_menu_text
from .embeddings import embed_texts_or_none
from .health_schema import (
    health_schema_configured, is_health_domain, read_original_filename, record_parse_error,
    write_chunks,
)
from .ingest import split_chunks
from .memory import try_remember
from .parsers import parse_file
from .relations import note_id_for, store_relations
from .tenancy import bind_knowledge_user
from ..models import (
    KnowledgeBatchItem, KnowledgeChunk, KnowledgeIngestJob, KnowledgeIngestStatus,
    KnowledgePendingAttachment, KnowledgeSource, KnowledgeStatus, KnowledgeUser,
    KnowledgeUserStatus,
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

#: ADR-021 фаза 2b: отдельная переменная от `_last_served_tenant_index` —
#: voice-pending и document-job — разные очереди, round-robin по одной не
#: должен влиять на другую.
_last_served_voice_tenant_index = -1

VOICE_TRANSCRIBE_FAILED_NOTICE = (
    "Не получилось расшифровать голосовое — попробуйте прислать ещё раз."
)


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


def claim_next_voice_pending(session: Session) -> KnowledgePendingAttachment | None:
    """Взять один voice-pending без транскрипта (ADR-021 фаза 2b) — тот же
    round-robin по тенантам, что `claim_next_job()`, отдельным индексом
    (`_last_served_voice_tenant_index`).

    `FOR UPDATE SKIP LOCKED` здесь держит блокировку строки на всё время
    вызова `process_voice_pending()` (транскрипция — медленный шаг,
    ~11с+), не только на сам SELECT — вызывающая сторона обязана не
    коммитить/закрывать сессию до конца обработки, тот же контракт, что
    уже есть у `claim_next_job()`/`process_job()`. Крах воркера ДО
    commit откатывает транзакцию — `transcript` остаётся NULL, строка
    снова доступна следующему опросу, тот же fail-safe, что у
    document-job'ов.
    """
    global _last_served_voice_tenant_index
    tenants = _active_tenant_ids(session)
    if not tenants:
        return None
    n = len(tenants)
    for offset in range(n):
        idx = (_last_served_voice_tenant_index + 1 + offset) % n
        tenant_id = tenants[idx]
        bind_knowledge_user(session, tenant_id)
        pending = session.scalar(
            select(KnowledgePendingAttachment)
            .where(KnowledgePendingAttachment.knowledge_user_id == tenant_id,
                  KnowledgePendingAttachment.kind == "voice",
                  KnowledgePendingAttachment.transcript.is_(None))
            .order_by(KnowledgePendingAttachment.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if pending is not None:
            _last_served_voice_tenant_index = idx
            return pending
    return None


def process_voice_pending(session: Session, pending: KnowledgePendingAttachment) -> None:
    """Асинхронная обработка одного voice-pending без транскрипта
    (ADR-021 фаза 2b, §14.10-14.11): транскрипция → проверка на Remember-
    команду → либо подтверждение Remember (вложение как документ больше
    не нужно), либо (не команда) — сохранить транскрипт и спросить домен,
    как для обычного документа, только теперь с текстом на руках
    (`voice_ready_menu_text()`). Не коммитит — вызывающий код решает
    транзакцию, тот же контракт, что `process_job()`.

    Один try/except на всё тело — тот же урок, что уже стоил живого
    краш-лупа у `process_job()` (29.08.2026): исключение после успешного
    шага не должно улетать необработанным из этой функции и ронять
    `run_forever()`. Не-Remember путь НЕ парсит файл здесь — второй раз
    (через `register_file_for_ingest()`) это сделает обычный document-
    pipeline, когда владелец ответит на вопрос о домене; сознательный
    компромисс "проще, но транскрибируем дважды" (ADR-021), не баг.
    """
    pending_id = pending.id
    channel = pending.channel
    recipient = pending.recipient
    spool_path = Path(pending.spool_path)

    try:
        transcript = transcribe_audio(spool_path)
        stripped = strip_timestamps(transcript)
        outcome = try_remember(session, channel=channel, text=stripped,
                               knowledge_user_id=pending.knowledge_user_id, origin_kind="voice")
        if outcome.status != "not_command":
            spool_path.unlink(missing_ok=True)
            notice = outcome.text
            reference = f"voice-remember-{outcome.status}:{pending_id}"
            session.delete(pending)
        else:
            pending.transcript = transcript
            notice = voice_ready_menu_text(session, pending)
            reference = f"voice-transcribed:{pending_id}"
    except Exception as exc:
        logger.warning("knowledge voice pending %s: обработка упала: %s", pending_id, exc)
        spool_path.unlink(missing_ok=True)
        session.delete(pending)
        notice = VOICE_TRANSCRIBE_FAILED_NOTICE
        reference = f"voice-failed:{pending_id}"

    if recipient:
        enqueue(session, channel=channel, recipient=recipient,
               reference=reference, payload_reference={"text": notice})


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

        # P8.5.6 слой 1 (E13, решение владельца 31.08.2026): [[wikilink]] +
        # явный YAML relations: в ИСХОДНОМ тексте (result.text), до того как
        # HELM допишет свой собственный frontmatter поверх него — тот же
        # текст, который видел бы Obsidian, открой владелец raw-файл.
        # ADR-005/P12: source.original_filename уже None для health (см.
        # register_file_for_ingest()) — note_id_for() откатывается на
        # source.id, что и требуется: from_id внутри health.knowledge_
        # relations не обязан совпадать с публичным именем файла.
        store_relations(session, domain=source.domain, knowledge_user_id=tenant_id,
                        from_id=note_id_for(original_filename=source.original_filename,
                                            source_id=source.id),
                        source_id=source.id, text=result.text)

        # ADR-019: L2 semantic atomizer, аддитивно поверх store_relations()
        # выше (fail-open, см. atomizer.py). vault_root восстанавливается из
        # source_path ("<vault_root>/sources/<sha256>.md") — тот же корень,
        # что был передан register_file_for_ingest(), не жёстко зашитый
        # DEFAULT_VAULT_ROOT (тесты регистрируют файл с vault_root=tmp_path).
        vault_root = str(Path(source.source_path).parent.parent)
        atomize_and_store(session, domain=source.domain, knowledge_user_id=tenant_id,
                          source_id=source.id, source_sha256=source.sha256, text=result.text,
                          vault_root=vault_root)

        chunks = split_chunks(result.text)
        # ADR-025: та же fail-open политика, что ingest_text() — сбой
        # embed-сервиса не должен превращать job в FAILED, чанк просто
        # остаётся без embedding до бэкафилла.
        embeddings = embed_texts_or_none(chunks)
        if is_health_domain(source.domain) and health_schema_configured():
            chunk_count = write_chunks(source_id=source.id, knowledge_user_id=tenant_id,
                                       chunks=chunks, embeddings=embeddings)
        else:
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
        error_detail = f"{type(exc).__name__}: {exc}"
        if is_health_domain(source.domain) and health_schema_configured():
            # Решение владельца при разборе P12, acceptance #7: полный
            # диагностический текст (может процитировать содержимое
            # документа через сообщение исключения парсера) — только в
            # health-sidecar. public.knowledge_ingest_jobs.error получает
            # исключительно санитизированный код.
            record_parse_error(source_id=source.id, knowledge_user_id=tenant_id,
                              message=error_detail)
            job.error = "HEALTH_PARSE_FAILED"
        else:
            job.error = error_detail
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
    # ADR-005/P12: source.original_filename — None для health (см.
    # register_file_for_ingest()). Уведомление уходит ТОЛЬКО владельцу
    # этого же source (channel/recipient взяты из его же job'а) — это то
    # самое законное "same-user disclosure", не утечка чужому chief/
    # helm_app, которым health-схема физически недоступна.
    if is_health_domain(source.domain) and health_schema_configured():
        filename = read_original_filename(source_id=source.id,
                                          knowledge_user_id=source.knowledge_user_id) or "без имени"
    else:
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
                if job is not None:
                    process_job(session, job)
                    session.commit()
                    logger.info("knowledge ingest job %s -> %s", job.id, job.status)
                    continue

                # ADR-021 фаза 2b: voice-pending опрашивается ПОСЛЕ
                # document-job'ов — голосовые заметки не должны отодвигать
                # уже поставленные в очередь документы, они и так медленнее
                # любого одиночного document-job'а (транскрипция, ~11с+).
                pending = claim_next_voice_pending(session)
                if pending is not None:
                    process_voice_pending(session, pending)
                    session.commit()
                    logger.info("knowledge voice pending %s обработан", pending.id)
                    continue

                session.commit()
                time.sleep(POLL_INTERVAL_SECONDS)
        except Exception:
            logger.exception("knowledge ingest worker: необработанная ошибка цикла")
            time.sleep(POLL_INTERVAL_SECONDS)
