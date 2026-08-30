"""Приём вложений из чата — Telegram/MAX (ТЗ §14.5.1, P8.5.7).

Двухшаговый диалог (решение владельца 29.08.2026, а не угадывание домена
по caption'у): байты сохраняются в защищённый spool СРАЗУ, до какого-либо
решения о домене — это буквально требование спеки "must be preserved
before any parser/LLM sees them", не только "before the parser". Домен
для файла не имеет спекой заданной конвенции ввода, поэтому HELM
спрашивает явно, а не угадывает — namespace несёт реальные ACL-последствия
(§14.15: `health` требует отдельного контура, `simpas/zapiski` обязан
получить `client_restricted` и не течь в общий поиск), молчаливый дефолт
здесь был бы нарушением §5.1/§5.2 верхнеуровневых правил агента, а не
только этой спеки.

`stage_attachment()` — вызывается ОБОИХ каналами в момент, когда пришёл
файл: пишет байты в spool, создаёт `KnowledgePendingAttachment`, дальше
вызывающая сторона отправляет `format_domain_menu()` владельцу и НЕ
запускает обычный pipeline (register_task/probe/chief) для этого
сообщения — вложение обрабатывается отдельно от диалога с chief.

`resolve_pending_domain()` — вызывается на СЛЕДУЮЩЕЕ текстовое сообщение
того же канала ДО обычного pipeline: если есть неразрешённое вложение —
это сообщение ЛИБО валидный ответ (домен/номер/отмена), ЛИБО нет; в обоих
случаях владелец получает результат внутри этой функции, а не уходит к
chief. FIFO по `created_at` внутри канала — редкий случай двух
неразрешённых вложений подряд решается по очереди, не последним/первым
произвольно.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import KnowledgeDomain, KnowledgePendingAttachment, KnowledgeSensitivity, KnowledgeSource
from .ingest import DEFAULT_VAULT_ROOT, RegisterFileResult, register_file_for_ingest

#: §14.5.1: "bounded size". Telegram Bot API само не отдаёт файлы крупнее
#: 20MB обычному боту (getFile) — берём тот же потолок для обоих каналов,
#: чтобы поведение не расходилось между Telegram и MAX без причины;
#: пересмотреть, если живой тест на MAX покажет другой практический лимит.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

DEFAULT_SPOOL_ROOT = "/opt/helm-state/knowledge-spool"

logger = logging.getLogger(__name__)

_DOMAINS = list(KnowledgeDomain)
_CANCEL_SENTINEL = "__cancel__"
_CANCEL_WORDS = {"отмена", "cancel", "нет", "no"}

#: Короткие псевдонимы для доменов, неудобных для набора на телефоне
#: (`/` и `-` внутри значения) — найдено живым использованием 29.08.2026,
#: владелец пытался набрать "simpas/company". Только для доменов, где
#: полное значение реально длиннее/неудобнее алиаса — `personal`/`health`/
#: `ventures`/`engineering`/`library` уже короткие однословные, алиас им
#: не нужен.
_DOMAIN_ALIASES: dict[str, str] = {
    "company": KnowledgeDomain.SIMPAS_COMPANY.value,
    "practice": KnowledgeDomain.SIMPAS_PRACTICE.value,
    "zapiski": KnowledgeDomain.SIMPAS_ZAPISKI.value,
    "moments": KnowledgeDomain.SIMPAS_MOMENTS.value,
    "marketing": KnowledgeDomain.PSY_MARKETING.value,
    "docs": KnowledgeDomain.SIGNALAI_DOCS.value,
}
#: Обратная карта для меню — какой алиас показать рядом с полным именем.
_ALIAS_BY_DOMAIN: dict[str, str] = {v: k for k, v in _DOMAIN_ALIASES.items()}


class AttachmentTooLarge(Exception):
    def __init__(self, size: int, limit: int):
        self.size = size
        self.limit = limit
        super().__init__(f"вложение {size} байт превышает лимит {limit} байт")


def format_domain_menu(original_filename: str | None) -> str:
    """Текст владельцу сразу после получения файла — до какого-либо парсинга."""
    lines = [
        f"Файл «{original_filename or 'без имени'}» получен и сохранён.",
        "В какой домен положить? Ответьте номером или именем:",
    ]
    for i, d in enumerate(_DOMAINS, 1):
        alias = _ALIAS_BY_DOMAIN.get(d.value)
        label = f"{d.value} ({alias})" if alias else d.value
        lines.append(f"{i}. {label}")
    lines.append("Или «отмена» — файл не будет сохранён.")
    return "\n".join(lines)


def parse_domain_reply(text: str) -> str | None:
    """Канонический domain (значение enum), `_CANCEL_SENTINEL`, или None,
    если ответ не распознан — вызывающая сторона обязана в этом случае
    повторить меню, а не угадывать намерение владельца."""
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.casefold() in _CANCEL_WORDS:
        return _CANCEL_SENTINEL
    if stripped.isdigit():
        idx = int(stripped)
        if 1 <= idx <= len(_DOMAINS):
            return _DOMAINS[idx - 1].value
        return None
    lowered = stripped.casefold()
    if lowered in _DOMAIN_ALIASES:
        return _DOMAIN_ALIASES[lowered]
    for d in _DOMAINS:
        if d.value.casefold() == lowered:
            return d.value
    return None


def stage_attachment(session: Session, *, channel: str, data: bytes,
                     original_filename: str | None, mime_type: str | None,
                     caption: str | None = None,
                     spool_root: str = DEFAULT_SPOOL_ROOT) -> KnowledgePendingAttachment:
    """§14.5.1 spool: owner-only каталог, bounded size. Имя файла в spool —
    случайный token, НЕ sha256: два вложения с одинаковым содержимым,
    ожидающие ответа одновременно (FIFO), не должны делить один physical
    файл — иначе резолв первого (rename) оставляет второе указывающим в
    никуда."""
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentTooLarge(len(data), MAX_ATTACHMENT_BYTES)

    sha256 = hashlib.sha256(data).hexdigest()
    ext = Path(original_filename).suffix if original_filename else ""
    spool_dir = Path(spool_root)
    spool_dir.mkdir(parents=True, exist_ok=True)
    spool_path = spool_dir / f"{uuid.uuid4().hex}{ext}"
    spool_path.write_bytes(data)

    pending = KnowledgePendingAttachment(
        channel=channel, sha256=sha256, spool_path=str(spool_path),
        original_filename=original_filename, mime_type=mime_type, caption=caption,
    )
    session.add(pending)
    session.flush()
    return pending


@dataclass
class ResolveOutcome:
    status: Literal["not_pending", "cancelled", "invalid", "missing", "failed",
                    "ingested", "duplicate"]
    result: RegisterFileResult | None = None
    pending: KnowledgePendingAttachment | None = None


def resolve_pending_domain(session: Session, *, channel: str, reply_text: str,
                           recipient: str | None = None,
                           vault_root: str = DEFAULT_VAULT_ROOT) -> ResolveOutcome:
    """Вызывается ДО обычного register_task/probe/chief pipeline. Возврат
    `not_pending` означает «это сообщение не про вложение» — вызывающая
    сторона продолжает обычный путь как раньше.

    `recipient` — chat_id/адресат, куда воркер пришлёт уведомление о
    завершении разбора (P8.5.7, третий шаг). Не обязателен: без него
    ingest всё равно проходит, просто уведомления о завершении не будет.
    """
    pending = session.scalar(
        select(KnowledgePendingAttachment)
        .where(KnowledgePendingAttachment.channel == channel)
        .order_by(KnowledgePendingAttachment.created_at)
        .limit(1)
    )
    if pending is None:
        return ResolveOutcome(status="not_pending")

    parsed = parse_domain_reply(reply_text)
    if parsed is None:
        return ResolveOutcome(status="invalid", pending=pending)

    if parsed == _CANCEL_SENTINEL:
        Path(pending.spool_path).unlink(missing_ok=True)
        session.delete(pending)
        session.flush()
        return ResolveOutcome(status="cancelled", pending=pending)

    domain = parsed
    spool_path = Path(pending.spool_path)
    if not spool_path.exists():
        # Аномалия (файл пропал из spool не через этот код) — не молчим и
        # не пытаемся угадать, снимаем запись и просим прислать заново.
        session.delete(pending)
        session.flush()
        return ResolveOutcome(status="missing", pending=pending)

    # НАЙДЕНО 30.08.2026 (вопрос владельца про повторную отправку файла):
    # SHA256-дедуп в register_file_for_ingest() уже не создаёт вторую
    # ingest job для того же содержимого — но ТОЛЬКО если до него дойти.
    # Проверка здесь, ДО перемещения из spool в raw/<domain>/, нужна по
    # двум причинам сразу: (1) без неё resolve_outcome_text() врал бы
    # "Разбор запущен" на файл, для которого job вообще не создастся;
    # (2) если тот же файл отправить повторно в ДРУГОЙ домен, копия всё
    # равно успевала бы физически лечь в raw/<новый_домен>/ (имя файла на
    # диске детерминировано sha256, а не домен) — источником в БД
    # оставался бы первый домен, файл во втором становился сиротой,
    # которую ничто не чистит. Дедуп по sha256 глобальный (не per-domain,
    # см. register_file_for_ingest) — если владелец РЕАЛЬНО хочет тот же
    # файл в другой домен, менять domain существующего source — отдельная
    # операция, не эта функция; здесь только "не создавай дубликат втихую".
    existing_source = session.scalar(
        select(KnowledgeSource).where(KnowledgeSource.sha256 == pending.sha256)
    )
    if existing_source is not None:
        spool_path.unlink(missing_ok=True)
        session.delete(pending)
        session.flush()
        return ResolveOutcome(
            status="duplicate", pending=pending,
            result=RegisterFileResult(source=existing_source, job=None, created=False),
        )

    raw_dir = Path(vault_root) / "raw" / domain
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{pending.sha256}{spool_path.suffix}"
    # НАЙДЕНО живым тестом 29.08.2026: spool (/opt/helm-state) и Vault
    # (/opt/helm-knowledge) на реальном сервере — РАЗНЫЕ файловые системы,
    # `os.replace()` падает `OSError: [Errno 18] Invalid cross-device
    # link` — атомарный rename попросту не работает между точками
    # монтирования, независимо от прав. §14.5.1 требует "atomic rename"
    # не ради самого rename, а ради гарантии "raw_path либо не существует,
    # либо содержит ПОЛНЫЙ файл, никогда не частичный" — та же гарантия
    # достигается копированием во временный файл НА ЦЕЛЕВОЙ файловой
    # системе (raw_dir) с последующим os.replace ВНУТРИ неё (это уже
    # гарантированно один диск) и удалением исходника только после
    # успешного rename. Работает одинаково что на одном диске, что на
    # разных — больше не полагаемся на топологию монтирования сервера.
    tmp_path = raw_dir / f".{pending.sha256}{spool_path.suffix}.part-{uuid.uuid4().hex}"
    try:
        shutil.copyfile(spool_path, tmp_path)
        os.replace(tmp_path, raw_path)
        spool_path.unlink()
    except OSError:
        logger.exception("chat_intake: не удалось перенести вложение %s в %s",
                         spool_path, raw_path)
        tmp_path.unlink(missing_ok=True)
        return ResolveOutcome(status="failed", pending=pending)

    # §14.15: ЗАПИСКИ — "not indexed into general namespaces" — client
    # content принудительно client_restricted независимо от того, что
    # владелец мог бы прислать в качестве caption; sensitivity здесь не
    # выбирается диалогом, только домен.
    sensitivity = (KnowledgeSensitivity.CLIENT_RESTRICTED.value
                  if domain == KnowledgeDomain.SIMPAS_ZAPISKI.value
                  else "internal")

    result = register_file_for_ingest(
        session, domain=domain, raw_path=raw_path,
        original_filename=pending.original_filename, mime_type=pending.mime_type,
        sensitivity=sensitivity, channel=channel, recipient=recipient, vault_root=vault_root,
    )
    session.delete(pending)
    session.flush()
    return ResolveOutcome(status="ingested", result=result, pending=pending)


#: Тексты владельцу для каждого исхода resolve_pending_domain()/
#: stage_attachment() — общие для ОБОИХ каналов (MAX — /hooks/max
#: in-process, Telegram — /internal/knowledge/attachment/* по HTTP,
#: P8.5.7). Единственное место, где эти строки записаны — расхождение
#: формулировок между каналами уже не раз стоило отдельного раунда
#: отладки в этой сессии (cross-channel дедуп, домены), здесь оно
#: структурно невозможно.
ATTACHMENT_TOO_LARGE_NOTICE = "Файл слишком большой — не сохранён."
ATTACHMENT_MISSING_NOTICE = "Файл потерян на сервере — пришлите, пожалуйста, ещё раз."
ATTACHMENT_MOVE_FAILED_NOTICE = (
    "Не получилось сохранить файл — попробуйте выбрать домен ещё раз."
)
ATTACHMENT_CANCELLED_NOTICE = "Хорошо, не сохраняю."


def resolve_outcome_text(outcome: ResolveOutcome) -> str | None:
    """Текст владельцу для исхода `resolve_pending_domain()`. None —
    только для `not_pending`: вызывающая сторона продолжает обычный
    register()/probe()/chief pipeline, ответа от диалога вложений нет."""
    if outcome.status == "ingested":
        return (f"Сохранено в «{outcome.result.source.domain}». "
               "Разбор запущен, появится в базе знаний в фоне.")
    if outcome.status == "duplicate":
        return (f"Этот файл уже есть в базе (домен «{outcome.result.source.domain}») — "
               "повторно не сохраняю и не разбираю.")
    if outcome.status == "cancelled":
        return ATTACHMENT_CANCELLED_NOTICE
    if outcome.status == "missing":
        return ATTACHMENT_MISSING_NOTICE
    if outcome.status == "failed":
        return ATTACHMENT_MOVE_FAILED_NOTICE
    if outcome.status == "invalid":
        return format_domain_menu(outcome.pending.original_filename)
    return None  # not_pending
