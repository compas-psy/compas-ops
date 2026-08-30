"""ZIP batch ingest (v3.7 §14.4.0/§14.5.1-2, `V3.7-DELTA.md`) — durable
batch/container layer ПЕРЕД уже работающим одиночным child-pipeline
(`chat_intake.py`/`ingest.py`/`worker.py`), не замена ему.

Жизненный цикл одного архива:

    stage_batch()           байты сохранены, preflight посчитан,
                             owner получает меню доменов (1 раз на архив)
    resolve_batch_domain()  следующее сообщение того же канала — домен
                             применяется КО ВСЕМ eligible-членам,
                             extract_member() потоково пишет каждый в
                             raw/<domain>/, register_file_for_ingest()
                             заводит child KnowledgeSource+job (тот же
                             путь, что и одиночное вложение)
    (worker.py, асинхронно)  каждый child job идёт по неизменному
                             pipeline; по завершении каждого —
                             finalize_batch_if_terminal() проверяет, не
                             пора ли отправить единственное финальное
                             уведомление (§14.5.2 exactly-once)

Домен спрашивается РОВНО ОДИН РАЗ на архив (§14.5.1) — намеренно НЕ
переиспользуется `KnowledgePendingAttachment` (та таблица — для
одиночных вложений, здесь состояние живёт в самом
`KnowledgeIngestBatch.status` через `WAITING_DOMAIN`, отдельная pending-
таблица не нужна, CLAUDE.md §2).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import zip_safety
from .chat_intake import _CANCEL_SENTINEL, domain_list_lines, parse_domain_reply
from .ingest import DEFAULT_VAULT_ROOT, register_file_for_ingest
from ..models import (
    BATCH_ITEM_TERMINAL_STATUSES, KnowledgeBatchItem, KnowledgeBatchItemStatus,
    KnowledgeBatchStatus, KnowledgeDomain, KnowledgeIngestBatch, KnowledgeIngestJob,
    KnowledgeIngestStatus, KnowledgeSensitivity,
)
from ..models.base import utcnow
from ..outbox import enqueue

logger = logging.getLogger(__name__)

DEFAULT_RAW_BATCHES_ROOT = "/opt/helm-knowledge/raw-batches"

#: v1: то же bounded-size соображение, что уже есть у одиночных
#: вложений (chat_intake.MAX_ATTACHMENT_BYTES), но для архива целиком —
#: значение спеки §14.7.6, не то же число, что у одного файла.
MAX_ARCHIVE_BYTES = zip_safety.MAX_ARCHIVE_BYTES


#: §14.4.0: "ZIP must no longer be treated as a MarkItDown document
#: format" — эта проверка должна отработать РАНЬШЕ роутера парсеров и
#: раньше одиночного chat_intake.py диалога, в обоих каналах (MAX —
#: `hooks.py`, Telegram — `helm-control`), одной и той же функцией,
#: чтобы критерий не разошёлся между ними.
_ZIP_MIME_TYPES = {"application/zip", "application/x-zip-compressed", "application/x-zip"}


def is_zip_attachment(original_filename: str | None, mime_type: str | None) -> bool:
    if mime_type in _ZIP_MIME_TYPES:
        return True
    return bool(original_filename) and original_filename.lower().endswith(".zip")


class ArchiveTooLarge(Exception):
    def __init__(self, size: int, limit: int):
        self.size = size
        self.limit = limit
        super().__init__(f"архив {size} байт превышает лимит {limit} байт")


def format_batch_domain_menu(archive_filename: str | None,
                             decisions: list[zip_safety.MemberDecision]) -> str:
    eligible = sum(1 for d in decisions if d.eligible)
    skipped = len(decisions) - eligible
    lines = [f"Архив «{archive_filename or 'без имени'}» получен: {len(decisions)} файлов."]
    if skipped:
        lines.append(f"{eligible} будут обработаны, {skipped} пропущено (формат/безопасность).")
    lines.append("В какой домен положить всё содержимое? Ответьте номером или именем:")
    lines.extend(domain_list_lines())
    lines.append("Или «отмена» — архив не будет обработан.")
    return "\n".join(lines)


def _previous_batch_reference_text(batch: KnowledgeIngestBatch) -> str:
    return (f"Такой архив уже обрабатывался (домен «{batch.domain}», "
           f"статус: {batch.status}). Повторно не разбираю.")


@dataclass
class StageBatchResult:
    batch: KnowledgeIngestBatch
    text: str
    #: True — новый архив (ждёт домена); False — это либо уже виденный
    #: архив (Layer-1 дедуп §14.6), либо BLOCKED на preflight.
    waiting_for_domain: bool


def stage_batch(session: Session, *, channel: str, data: bytes,
                original_filename: str | None, mime_type: str | None,
                recipient: str | None = None,
                raw_batches_root: str = DEFAULT_RAW_BATCHES_ROOT) -> StageBatchResult:
    """Сохранить архив, посчитать preflight, вернуть текст для владельца.

    Не создаёт `KnowledgeBatchItem` строки — они заводятся в
    `resolve_batch_domain()`, когда домен уже известен, чтобы не тратить
    работу на архив, который может быть отменён на этом же шаге.
    """
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArchiveTooLarge(len(data), MAX_ARCHIVE_BYTES)

    archive_sha256 = hashlib.sha256(data).hexdigest()

    # §14.6 "ZIP-specific dedup", Layer 1: тот же архив уже видели —
    # не разворачиваем повторно, просто ссылаемся на прошлый результат.
    existing = session.scalar(
        select(KnowledgeIngestBatch)
        .where(KnowledgeIngestBatch.archive_sha256 == archive_sha256)
        .order_by(KnowledgeIngestBatch.created_at.desc())
        .limit(1)
    )
    if existing is not None and existing.status != KnowledgeBatchStatus.WAITING_DOMAIN:
        return StageBatchResult(batch=existing, text=_previous_batch_reference_text(existing),
                                waiting_for_domain=False)

    batch = KnowledgeIngestBatch(
        channel=channel, recipient=recipient, archive_filename=original_filename,
        archive_mime=mime_type, archive_size_bytes=len(data), archive_raw_path="",
        archive_sha256=archive_sha256, status=KnowledgeBatchStatus.HASHING,
    )
    session.add(batch)
    session.flush()

    raw_dir = Path(raw_batches_root) / str(batch.id)
    raw_dir.mkdir(parents=True, exist_ok=True)
    archive_path = raw_dir / "original.zip"
    archive_path.write_bytes(data)
    batch.archive_raw_path = str(archive_path)

    batch.status = KnowledgeBatchStatus.ARCHIVE_PREFLIGHT
    try:
        decisions = zip_safety.preflight(archive_path)
    except zip_safety.ArchiveBlocked as exc:
        batch.status = KnowledgeBatchStatus.BLOCKED
        batch.error_code = exc.code
        session.flush()
        return StageBatchResult(
            batch=batch,
            text=f"Архив «{original_filename or 'без имени'}» не принят: {exc}.",
            waiting_for_domain=False,
        )

    batch.total_members = len(decisions)
    batch.eligible_members = sum(1 for d in decisions if d.eligible)
    batch.status = KnowledgeBatchStatus.WAITING_DOMAIN
    session.flush()

    return StageBatchResult(batch=batch, text=format_batch_domain_menu(original_filename, decisions),
                            waiting_for_domain=True)


@dataclass
class ResolveBatchOutcome:
    status: Literal["not_pending", "cancelled", "invalid", "blocked", "queued"]
    batch: KnowledgeIngestBatch | None = None


def resolve_batch_domain(session: Session, *, channel: str, reply_text: str,
                         vault_root: str = DEFAULT_VAULT_ROOT) -> ResolveBatchOutcome:
    """Следующее сообщение канала после `stage_batch()` — тот же паттерн
    диалога, что `chat_intake.resolve_pending_domain()`, но для batch:
    ищет `KnowledgeIngestBatch` в `WAITING_DOMAIN` на этом канале (не
    `KnowledgePendingAttachment` — разные таблицы, разные диалоги)."""
    batch = session.scalar(
        select(KnowledgeIngestBatch)
        .where(KnowledgeIngestBatch.channel == channel,
              KnowledgeIngestBatch.status == KnowledgeBatchStatus.WAITING_DOMAIN)
        .order_by(KnowledgeIngestBatch.created_at)
        .limit(1)
    )
    if batch is None:
        return ResolveBatchOutcome(status="not_pending")

    parsed = parse_domain_reply(reply_text)
    if parsed is None:
        return ResolveBatchOutcome(status="invalid", batch=batch)

    if parsed == _CANCEL_SENTINEL:
        batch.status = KnowledgeBatchStatus.CANCELLED
        session.flush()
        return ResolveBatchOutcome(status="cancelled", batch=batch)

    domain = parsed
    batch.domain = domain
    # §14.15/chat_intake.py: тот же принудительный sensitivity для
    # simpas/zapiski, не вводим отдельное понятие security_scope.
    batch.sensitivity = (KnowledgeSensitivity.CLIENT_RESTRICTED.value
                         if domain == KnowledgeDomain.SIMPAS_ZAPISKI.value
                         else "internal")

    try:
        decisions = zip_safety.preflight(Path(batch.archive_raw_path))
    except zip_safety.ArchiveBlocked as exc:
        # Не должно случиться повторно (уже прошло preflight в
        # stage_batch), но файл на диске мог исчезнуть/испортиться между
        # шагами — не молчим, тот же контракт, что cross-device failure
        # в chat_intake.py.
        batch.status = KnowledgeBatchStatus.BLOCKED
        batch.error_code = exc.code
        session.flush()
        return ResolveBatchOutcome(status="blocked", batch=batch)

    batch.status = KnowledgeBatchStatus.EXPANDING
    session.flush()

    for decision in decisions:
        item = KnowledgeBatchItem(
            batch_id=batch.id, ordinal=decision.ordinal,
            archive_member_path_original=decision.path_original,
            archive_member_name_normalized=decision.path_normalized,
            declared_compressed_size=decision.declared_compressed_size,
            declared_uncompressed_size=decision.declared_uncompressed_size,
        )
        session.add(item)
        session.flush()
        _process_item(session, batch, item, decision, vault_root=vault_root)

    batch.status = KnowledgeBatchStatus.PROCESSING
    session.flush()
    finalize_batch_if_terminal(session, batch.id)
    return ResolveBatchOutcome(status="queued", batch=batch)


def batch_resolve_outcome_text(outcome: "ResolveBatchOutcome") -> str | None:
    """Текст владельцу для исхода `resolve_batch_domain()` — единственное
    место для ОБОИХ каналов (MAX/Telegram), тот же принцип, что уже есть у
    `chat_intake.resolve_outcome_text()` для одиночных вложений (расхождение
    формулировок между каналами в этой сессии уже не раз стоило отдельного
    раунда отладки)."""
    if outcome.status == "invalid" and outcome.batch is not None:
        decisions = zip_safety.preflight(Path(outcome.batch.archive_raw_path))
        return format_batch_domain_menu(outcome.batch.archive_filename, decisions)
    if outcome.status == "cancelled":
        return "Хорошо, архив не обрабатываю."
    if outcome.status == "blocked" and outcome.batch is not None:
        return f"Архив не обработан: {outcome.batch.error_code}."
    if outcome.status == "queued" and outcome.batch is not None:
        return (f"Поставил в очередь {outcome.batch.eligible_members} документов "
               f"из {outcome.batch.total_members}. "
               "Напишу, когда пакет обработается полностью.")
    return None  # not_pending


def _process_item(session: Session, batch: KnowledgeIngestBatch, item: KnowledgeBatchItem,
                  decision: zip_safety.MemberDecision, *, vault_root: str) -> None:
    """Обработать один eligible-член: потоково извлечь, зарегистрировать
    через УЖЕ РАБОТАЮЩИЙ `register_file_for_ingest()` (тот же SHA256-
    дедуп, что и у одиночных вложений — ничего нового не изобретаем).
    Не-eligible член просто получает свой terminal-статус, извлекать
    нечего. Общая функция для `resolve_batch_domain()` (первый проход) и
    `retry_failed()` (повторный проход по конкретным FAILED item'ам) —
    один код пути, не два места, которые могут разойтись."""
    if not decision.eligible:
        item.status = KnowledgeBatchItemStatus(decision.status)
        item.error_detail_redacted = decision.reason
        item.graph_status = "not_applicable"
        return

    raw_dir = Path(vault_root) / "raw" / batch.domain
    ext = Path(decision.path_normalized).suffix
    archive_path = Path(batch.archive_raw_path)
    # Промежуточное имя по item.id — sha256 известен только ПОСЛЕ
    # потокового чтения (extract_member сам его считает); переименование
    # в финальный sha256-путь — внутри одной директории, гарантированно
    # один диск, не нужен os.replace между файловыми системами.
    member_dest = raw_dir / f".batch-item-{item.id}{ext}"
    try:
        member_sha256 = zip_safety.extract_member(archive_path, decision, member_dest)
    except (zip_safety.ArchiveBlocked, zipfile.BadZipFile, OSError) as exc:
        item.status = KnowledgeBatchItemStatus.FAILED
        item.retryable = True
        item.error_code = getattr(exc, "code", type(exc).__name__)
        item.error_detail_redacted = str(exc)
        item.graph_status = "not_applicable"
        return

    item.member_sha256 = member_sha256
    final_dest = raw_dir / f"{member_sha256}{ext}"
    if final_dest.exists():
        # Байты уже есть на диске под этим sha256 (другой source ранее)
        # — register_file_for_ingest() найдёт существующий source по
        # sha256, наш временный файл ему не нужен.
        member_dest.unlink(missing_ok=True)
    else:
        member_dest.rename(final_dest)

    result = register_file_for_ingest(
        session, domain=batch.domain, raw_path=final_dest,
        original_filename=Path(decision.path_normalized).name,
        mime_type=decision.detected_mime, sensitivity=batch.sensitivity,
        # channel/recipient НАМЕРЕННО None — per-item уведомление не
        # нужно (§14.5.2 "No per-file push spam"); worker.py::
        # _notify_owner_of_result() уже тихо no-op, если их нет.
        channel=None, recipient=None, vault_root=vault_root,
    )
    item.source_id = result.source.id
    item.source_created_by_batch = result.created
    if result.job is None:
        item.status = KnowledgeBatchItemStatus.EXACT_DUPLICATE
        item.graph_status = "not_applicable"
    else:
        item.status = KnowledgeBatchItemStatus.QUEUED
        item.retryable = False
        result.job.batch_item_id = item.id


def sync_item_from_job(session: Session, item: KnowledgeBatchItem, *,
                       job_status: str, chunk_count: int, job_error: str | None) -> None:
    """Вызывается `worker.py::process_job()` по завершении child job,
    заведённого batch'ем (`job.batch_item_id` заполнен). Переносит
    результат разбора в терминальный статус item — Graphify не
    реализован (P8.5.6), поэтому READY тут же терминален, не ждёт
    несуществующей стадии графа."""
    if job_status == KnowledgeIngestStatus.DONE:
        item.status = KnowledgeBatchItemStatus.READY
        item.chunks = chunk_count
    elif job_status == KnowledgeIngestStatus.NEEDS_REVIEW:
        item.status = KnowledgeBatchItemStatus.FAILED
        item.retryable = True
        item.error_code = "NEEDS_REVIEW"
        item.error_detail_redacted = "не удалось надёжно извлечь текст"
    else:
        item.status = KnowledgeBatchItemStatus.FAILED
        item.retryable = True
        item.error_code = "PARSE_FAILED"
        item.error_detail_redacted = job_error
    item.graph_status = "not_applicable"


def _batch_counts(items: list[KnowledgeBatchItem]) -> Counter:
    return Counter(i.status for i in items)


def _apply_counts(batch: KnowledgeIngestBatch, items: list[KnowledgeBatchItem]) -> None:
    counts = _batch_counts(items)
    batch.ready_count = counts.get(KnowledgeBatchItemStatus.READY.value, 0)
    batch.duplicate_count = counts.get(KnowledgeBatchItemStatus.EXACT_DUPLICATE.value, 0)
    batch.failed_count = counts.get(KnowledgeBatchItemStatus.FAILED.value, 0)
    batch.quarantine_count = counts.get(KnowledgeBatchItemStatus.QUARANTINE.value, 0)
    batch.skipped_count = (
        counts.get(KnowledgeBatchItemStatus.SKIPPED_UNSUPPORTED.value, 0)
        + counts.get(KnowledgeBatchItemStatus.SKIPPED_NESTED_ARCHIVE.value, 0)
        + counts.get(KnowledgeBatchItemStatus.SKIPPED_CANCELLED.value, 0)
    )
    batch.chunk_count_total = sum(i.chunks or 0 for i in items)


def _final_summary_text(batch: KnowledgeIngestBatch) -> str:
    parts = [f"Архив «{batch.archive_filename or 'без имени'}» готов."]
    pieces = []
    if batch.ready_count:
        pieces.append(f"{batch.ready_count} готово")
    if batch.duplicate_count:
        pieces.append(f"{batch.duplicate_count} уже было в базе")
    if batch.failed_count:
        pieces.append(f"{batch.failed_count} ошибка")
    if batch.quarantine_count:
        pieces.append(f"{batch.quarantine_count} заблокировано")
    if batch.skipped_count:
        pieces.append(f"{batch.skipped_count} пропущено")
    parts.append(f"{batch.total_members} файлов: " + " · ".join(pieces) + ".")
    if batch.chunk_count_total:
        parts.append(f"Создано {batch.chunk_count_total} частей.")
    return "\n".join(parts)


def finalize_batch_if_terminal(session: Session, batch_id: uuid.UUID) -> KnowledgeIngestBatch | None:
    """Пересчитать счётчики по факту (GROUP BY дешевле и надёжнее, чем
    инкремент под гонку двух воркеров — членов не больше MAX_MEMBERS=500)
    и, если ВСЕ item terminal, отправить единственное финальное
    уведомление (§14.5.2 exactly-once). Безопасно вызывать многократно
    (после каждого завершённого child job, и после падения/рестарта
    процесса) — второй вызов после уже отправленного уведомления просто
    ничего не делает, благодаря `final_notification_sent_at`.
    """
    batch = session.get(KnowledgeIngestBatch, batch_id)
    if batch is None:
        return None

    items = session.scalars(
        select(KnowledgeBatchItem).where(KnowledgeBatchItem.batch_id == batch_id)
    ).all()
    if not items:
        return batch

    _apply_counts(batch, items)

    all_terminal = all(i.status in BATCH_ITEM_TERMINAL_STATUSES for i in items)
    if not all_terminal or batch.final_notification_sent_at is not None:
        session.flush()
        return batch

    batch.status = (KnowledgeBatchStatus.COMPLETED_WITH_ERRORS
                    if batch.failed_count or batch.quarantine_count
                    else KnowledgeBatchStatus.COMPLETED)
    batch.finished_at = utcnow()

    if batch.channel and batch.recipient:
        try:
            enqueue(session, channel=batch.channel, recipient=batch.recipient,
                   reference=f"knowledge_batch_final:{batch.id}:{batch.completion_revision}",
                   payload_reference={"text": _final_summary_text(batch)})
        except Exception:
            logger.exception("batch %s: не удалось поставить финальное уведомление в очередь",
                             batch.id)
    batch.final_notification_sent_at = utcnow()
    session.flush()
    return batch


def retry_failed(session: Session, batch_id: uuid.UUID, *,
                 vault_root: str = DEFAULT_VAULT_ROOT) -> KnowledgeIngestBatch | None:
    """§14.5.2: только `FAILED` item'ы с `retryable=true` — READY/
    EXACT_DUPLICATE/QUARANTINE/SKIPPED_* не трогает. Увеличивает
    `completion_revision`, снимает `final_notification_sent_at` — новое
    финальное уведомление разрешено ровно для этого цикла ретрая, старый
    dedup_key (с прежним completion_revision) не мешает.

    Два разных провала требуют разного ретрая, а не одного и того же
    кода: если `item.source_id` уже заполнен, значит извлечение и
    регистрация УЖЕ прошли успешно в прошлый раз — провалился именно
    `parse_file()` внутри уже существующего job'а. Повторный вызов
    `_process_item()` в этом случае звал бы `register_file_for_ingest()`
    заново, тот находил бы уже существующий source по тому же sha256 и
    отдавал EXACT_DUPLICATE вместо повторного разбора — молча "чинил"
    провал в неверную сторону (найдено этим же тестовым прогоном).
    Правильный ретрай провала парсинга — просто перевзвести ТОТ ЖЕ job.
    Если `source_id` пуст — провал был на этапе извлечения из архива,
    источника ещё не существует, и `_process_item()` с нуля — корректный
    путь.
    """
    batch = session.get(KnowledgeIngestBatch, batch_id)
    if batch is None:
        return None

    retryable_items = session.scalars(
        select(KnowledgeBatchItem).where(
            KnowledgeBatchItem.batch_id == batch_id,
            KnowledgeBatchItem.status == KnowledgeBatchItemStatus.FAILED,
            KnowledgeBatchItem.retryable.is_(True),
        )
    ).all()
    if not retryable_items:
        return batch

    decisions_by_path = None  # считаем preflight лениво — не нужен, если все провалы на парсинге
    for item in retryable_items:
        if item.source_id is not None:
            job = session.scalar(
                select(KnowledgeIngestJob).where(KnowledgeIngestJob.batch_item_id == item.id)
            )
            if job is not None:
                job.status = KnowledgeIngestStatus.PENDING
                job.error = None
                item.status = KnowledgeBatchItemStatus.QUEUED
                item.retryable = False
                continue

        if decisions_by_path is None:
            decisions_by_path = {
                d.path_original: d for d in zip_safety.preflight(Path(batch.archive_raw_path))
            }
        decision = decisions_by_path.get(item.archive_member_path_original)
        if decision is None:
            continue  # архив на диске больше не содержит этот путь — не гадаем
        _process_item(session, batch, item, decision, vault_root=vault_root)

    batch.completion_revision += 1
    batch.final_notification_sent_at = None
    batch.status = KnowledgeBatchStatus.PROCESSING
    session.flush()
    finalize_batch_if_terminal(session, batch.id)
    return batch


def cancel_remaining(session: Session, batch_id: uuid.UUID) -> KnowledgeIngestBatch | None:
    """§final clarifications: очередь/не начатые члены -> SKIPPED_CANCELLED,
    завершённые остаются как есть — не откатываем READY/EXACT_DUPLICATE."""
    batch = session.get(KnowledgeIngestBatch, batch_id)
    if batch is None:
        return None

    queued_items = session.scalars(
        select(KnowledgeBatchItem).where(
            KnowledgeBatchItem.batch_id == batch_id,
            KnowledgeBatchItem.status == KnowledgeBatchItemStatus.QUEUED,
        )
    ).all()
    for item in queued_items:
        item.status = KnowledgeBatchItemStatus.SKIPPED_CANCELLED
        item.graph_status = "not_applicable"

    session.flush()
    finalize_batch_if_terminal(session, batch.id)
    return batch


def disable_created_sources(session: Session, batch_id: uuid.UUID) -> int:
    """§final clarifications: отключает ТОЛЬКО источники, реально
    созданные ЭТИМ batch'ем (`source_created_by_batch=true`), никогда
    пре-существовавший source, на который член оказался duplicate.
    "Мягкая блокировка" — тот же механизм, что уже есть у одиночных
    источников (`KnowledgeStatus.ARCHIVED`, уже исключён из `probe()`),
    не физическое удаление файлов."""
    from ..models import KnowledgeSource, KnowledgeStatus

    items = session.scalars(
        select(KnowledgeBatchItem).where(
            KnowledgeBatchItem.batch_id == batch_id,
            KnowledgeBatchItem.source_created_by_batch.is_(True),
            KnowledgeBatchItem.source_id.is_not(None),
        )
    ).all()
    count = 0
    for item in items:
        source = session.get(KnowledgeSource, item.source_id)
        if source is not None and source.status != KnowledgeStatus.ARCHIVED:
            source.status = KnowledgeStatus.ARCHIVED
            count += 1
    session.flush()
    return count
