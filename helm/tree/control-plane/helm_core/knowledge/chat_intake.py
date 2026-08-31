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

ADR-021 фаза 2b (voice-«Запомни»): для voice-вложений (`kind == "voice"`)
вопрос о домене откладывается — Remember-или-документ решается только
ПОСЛЕ фоновой транскрипции (`worker.py::process_voice_pending()`), не
здесь и не сразу. `stage_attachment()` для voice возвращает не меню
доменов, а уведомление "расшифровываю"; строка становится видимой
`resolve_pending_domain()` только когда `transcript` уже заполнен.
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

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import (
    KnowledgeCustomDomain, KnowledgeDomain, KnowledgePendingAttachment,
    KnowledgeSensitivity, KnowledgeSource,
)
from .audio import is_audio_file
from .ingest import DEFAULT_VAULT_ROOT, RegisterFileResult, register_file_for_ingest
from .quotas import QuotaExceeded
from .tenancy import bind_knowledge_user

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


#: Домен ограничен той же длиной, что колонка `domain` везде в схеме
#: (`String(32)`) — миграция 8b2f4e7a1c93. Не про безопасность, а про то,
#: чтобы явно отказать раньше, чем БД обрежет/уронит запись.
_MAX_CUSTOM_DOMAIN_LEN = 32


def _custom_domains(session: Session, knowledge_user_id: uuid.UUID) -> list[KnowledgeCustomDomain]:
    """Домены, которые ЭТОТ пользователь когда-то ввёл сам — "recent/
    most-used" половина реестра (§14.5). Порядок: по частоте, затем по
    свежести — то, чем чаще и недавнее пользуются, всплывает выше."""
    return list(session.scalars(
        select(KnowledgeCustomDomain)
        .where(KnowledgeCustomDomain.knowledge_user_id == knowledge_user_id)
        .order_by(KnowledgeCustomDomain.use_count.desc(),
                  KnowledgeCustomDomain.last_used_at.desc())
    ))


def domain_list_lines(session: Session, knowledge_user_id: uuid.UUID) -> list[str]:
    """Пронумерованный список доменов с алиасами — общая часть меню для
    одиночных вложений (`format_domain_menu`) и ZIP-batch
    (`batch_intake.py::format_batch_domain_menu`), чтобы формулировки не
    разошлись между ними (тот же риск, что уже стоил отладки для
    cross-channel текстов P8.5.7).

    Встроенные домены идут первыми и в фиксированном порядке — владелец
    уже помнит их номера наизусть, менять порядок ради "recent/most-
    used" означало бы ломать привычку ради домена, добавленного вчера.
    Домены, которые пользователь когда-то ввёл сам (реестр
    `knowledge_domains`, ADR-024, узкий срез), добавляются следом —
    именно они "recent/most-used" в буквальном смысле §14.5, встроенные
    добавлять в тот же счётчик незачем, они и так всегда на виду.
    """
    lines = []
    for i, d in enumerate(_DOMAINS, 1):
        alias = _ALIAS_BY_DOMAIN.get(d.value)
        label = f"{d.value} ({alias})" if alias else d.value
        lines.append(f"{i}. {label}")
    offset = len(_DOMAINS)
    for i, custom in enumerate(_custom_domains(session, knowledge_user_id), offset + 1):
        lines.append(f"{i}. {custom.key}")
    return lines


def format_domain_menu(session: Session, knowledge_user_id: uuid.UUID,
                       original_filename: str | None) -> str:
    """Текст владельцу сразу после получения файла — до какого-либо парсинга."""
    lines = [
        f"Файл «{original_filename or 'без имени'}» получен и сохранён.",
        "В какой домен положить? Ответьте номером или именем — можно",
        "новым, если подходящего домена в списке ещё нет:",
        *domain_list_lines(session, knowledge_user_id),
        "Или «отмена» — файл не будет сохранён.",
    ]
    return "\n".join(lines)


def parse_domain_reply(session: Session, knowledge_user_id: uuid.UUID, text: str) -> str | None:
    """Канонический domain, `_CANCEL_SENTINEL`, или None, если ответ не
    распознан — вызывающая сторона обязана в этом случае повторить меню,
    а не угадывать намерение владельца.

    §14.5 "No hardcoded domain enum": набранное имя, которое не встроено
    и не совпадает с уже существующим доменом ЭТОГО пользователя,
    создаёт новый — не отклоняется как invalid. Это не "силой из одного
    документа" (§14.5 "Do not silently create a permanent new domain
    from one document") — домен создаёт явный текстовый ответ на прямой
    вопрос меню, не догадка по содержимому файла.
    """
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.casefold() in _CANCEL_WORDS:
        return _CANCEL_SENTINEL

    customs = _custom_domains(session, knowledge_user_id)

    if stripped.isdigit():
        idx = int(stripped)
        combined_len = len(_DOMAINS) + len(customs)
        if 1 <= idx <= len(_DOMAINS):
            return _DOMAINS[idx - 1].value
        if len(_DOMAINS) < idx <= combined_len:
            custom = customs[idx - len(_DOMAINS) - 1]
            custom.use_count += 1
            return custom.key
        return None

    lowered = stripped.casefold()
    if lowered in _DOMAIN_ALIASES:
        return _DOMAIN_ALIASES[lowered]
    for d in _DOMAINS:
        if d.value.casefold() == lowered:
            return d.value
    for custom in customs:
        if custom.key.casefold() == lowered:
            custom.use_count += 1
            return custom.key

    if len(stripped) > _MAX_CUSTOM_DOMAIN_LEN or any(ch.isspace() for ch in stripped):
        # Домен — один токен (`personal`, `simpas/company`, `psy-
        # marketing` — ни один встроенный домен не содержит пробела).
        # Ответ из нескольких слов читается как случайный/непонятый
        # текст, не как имя домена, — старое поведение ("not a domain"
        # -> invalid) остаётся верным по той же причине, по которой оно
        # изначально было выбрано для теста.
        return None
    custom = KnowledgeCustomDomain(knowledge_user_id=knowledge_user_id, key=stripped)
    session.add(custom)
    session.flush()
    return custom.key


def stage_attachment(session: Session, *, channel: str, data: bytes,
                     original_filename: str | None, mime_type: str | None,
                     caption: str | None = None,
                     spool_root: str = DEFAULT_SPOOL_ROOT,
                     knowledge_user_id: uuid.UUID | None = None,
                     recipient: str | None = None) -> KnowledgePendingAttachment:
    """§14.5.1 spool: owner-only каталог, bounded size. Имя файла в spool —
    случайный token, НЕ sha256: два вложения с одинаковым содержимым,
    ожидающие ответа одновременно (FIFO), не должны делить один physical
    файл — иначе резолв первого (rename) оставляет второе указывающим в
    никуда.

    `knowledge_user_id=None` — существующие call sites (P8.6.2 Dedicated
    Knowledge Bot ещё не существует): разрешается в SYSTEM_OWNER.

    `recipient` — ADR-021 фаза 2b: адресат для АСИНХРОННОГО уведомления,
    которое пришлёт `worker.py::process_voice_pending()` после фоновой
    транскрипции voice-вложений (домен для них не спрашивается сразу —
    см. `kind` ниже). Для document-вложений не используется: их job уже
    получает recipient позже, из `resolve_pending_domain()`.
    """
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)

    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentTooLarge(len(data), MAX_ATTACHMENT_BYTES)

    sha256 = hashlib.sha256(data).hexdigest()
    ext = Path(original_filename).suffix if original_filename else ""
    spool_dir = Path(spool_root)
    spool_dir.mkdir(parents=True, exist_ok=True)
    spool_path = spool_dir / f"{uuid.uuid4().hex}{ext}"
    spool_path.write_bytes(data)

    # ADR-021 фаза 2b: то же определение "это аудио", что уже использует
    # parsers.py::parse_file() на физическом файле — voice-вложение решает
    # Remember-или-документ только ПОСЛЕ транскрипции (асинхронно), домен
    # для него сразу не спрашивается (см. stage_outcome_text()).
    kind = "voice" if original_filename and is_audio_file(Path(original_filename)) else "document"

    pending = KnowledgePendingAttachment(
        knowledge_user_id=knowledge_user_id,
        channel=channel, sha256=sha256, spool_path=str(spool_path),
        original_filename=original_filename, mime_type=mime_type, caption=caption,
        kind=kind, recipient=recipient,
    )
    session.add(pending)
    session.flush()
    return pending


@dataclass
class ResolveOutcome:
    status: Literal["not_pending", "cancelled", "invalid", "missing", "failed",
                    "ingested", "duplicate", "quota_exceeded"]
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

    Тенант привязывается ДО поиска pending-строки (P8.6.1 default —
    SYSTEM_OWNER), не после: `knowledge_pending_attachments` — tenant-
    scoped таблица под RLS (v3.8 §14.4), запрос ниже увидит только
    строки текущего тенанта. Пока Dedicated Knowledge Bot (P8.6.2) не
    существует, единственный вызывающий канал — SYSTEM_OWNER, так что
    порядок не теряет ни одной существующей pending-строки; когда
    появится Phase 2, определять тенанта (например, по verified
    `from.id` конкретного канала) придётся ДО этого запроса в любом
    случае — RLS не единственная причина, просто заставляет решить это
    раньше.
    """
    knowledge_user_id = bind_knowledge_user(session, None)

    # ADR-021 фаза 2b: voice-pending БЕЗ транскрипта (ещё не обработан
    # фоновым `worker.py::process_voice_pending()`) — не готов к вопросу о
    # домене вовсе, его нельзя отдавать сюда как "следующий неразрешённый".
    # Текстовый ответ, пришедший в эту паузу, должен провалиться в обычный
    # pipeline (`not_pending`), а не быть ошибочно понят как ответ на
    # домен для файла, который ещё даже не транскрибирован.
    pending = session.scalar(
        select(KnowledgePendingAttachment)
        .where(KnowledgePendingAttachment.channel == channel,
              or_(KnowledgePendingAttachment.kind != "voice",
                  KnowledgePendingAttachment.transcript.isnot(None)))
        .order_by(KnowledgePendingAttachment.created_at)
        .limit(1)
    )
    if pending is None:
        return ResolveOutcome(status="not_pending")

    parsed = parse_domain_reply(session, knowledge_user_id, reply_text)
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
        select(KnowledgeSource).where(
            KnowledgeSource.knowledge_user_id == knowledge_user_id,
            KnowledgeSource.sha256 == pending.sha256,
        )
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

    try:
        result = register_file_for_ingest(
            session, domain=domain, raw_path=raw_path,
            original_filename=pending.original_filename, mime_type=pending.mime_type,
            sensitivity=sensitivity, channel=channel, recipient=recipient, vault_root=vault_root,
            knowledge_user_id=knowledge_user_id,
        )
    except QuotaExceeded:
        # §14.4: файл уже физически перенесён в raw_path (atomic-move
        # выше, до самой проверки квоты — квота живёт в БД, не на уровне
        # файловой системы) — байты, на которые квоты не хватило, не
        # должны остаться сиротой на диске. spool_path уже удалён тем же
        # move'ом, так что повторный ответ на этот pending нашёл бы
        # несуществующий spool_path и сам получил бы "missing" — здесь
        # то же самое, просто сразу, не тратя ещё один раунд диалога.
        raw_path.unlink(missing_ok=True)
        session.delete(pending)
        session.flush()
        return ResolveOutcome(status="quota_exceeded", pending=pending)
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
ATTACHMENT_QUOTA_EXCEEDED_NOTICE = (
    "Квота хранилища/загрузки исчерпана — файл не сохранён. Обратитесь к владельцу."
)

#: ADR-021 фаза 2b — сразу после stage_attachment() для voice: домен ещё
#: не спрашивается (в отличие от document), решение Remember/документ
#: придёт асинхронно после фоновой транскрипции.
VOICE_STAGED_NOTICE = "Голосовое получено, расшифровываю — отвечу через несколько секунд."

#: Предпросмотр транскрипта перед вопросом о домене (не-Remember voice) —
#: полный текст ушёл бы за разумную длину сообщения чата на длинной
#: голосовой заметке, это только ориентир, что было распознано.
_VOICE_TRANSCRIPT_PREVIEW_CHARS = 300


def stage_outcome_text(session: Session, pending: KnowledgePendingAttachment) -> str:
    """Текст владельцу сразу после `stage_attachment()` — ветвится по
    `pending.kind` (ADR-021 фаза 2b): voice откладывает домен до фоновой
    транскрипции (см. `worker.py::process_voice_pending()`), document —
    прежнее поведение, домен сразу."""
    if pending.kind == "voice":
        return VOICE_STAGED_NOTICE
    return format_domain_menu(session, pending.knowledge_user_id, pending.original_filename)


def voice_ready_menu_text(session: Session, pending: KnowledgePendingAttachment) -> str:
    """После транскрипции voice-вложения, которое НЕ Remember-команда
    (ADR-021 фаза 2b) — предпросмотр распознанного текста и тот же вопрос
    о домене, что и для обычного документа. `pending.transcript` должен
    быть уже проставлен вызывающей стороной."""
    preview = (pending.transcript or "").strip()
    if len(preview) > _VOICE_TRANSCRIPT_PREVIEW_CHARS:
        preview = preview[:_VOICE_TRANSCRIPT_PREVIEW_CHARS].rstrip() + "…"
    return "\n".join([
        f"Расшифровка:\n{preview}",
        "",
        format_domain_menu(session, pending.knowledge_user_id, pending.original_filename),
    ])


def resolve_outcome_text(session: Session, outcome: ResolveOutcome) -> str | None:
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
    if outcome.status == "quota_exceeded":
        return ATTACHMENT_QUOTA_EXCEEDED_NOTICE
    if outcome.status == "invalid":
        return format_domain_menu(session, outcome.pending.knowledge_user_id,
                                  outcome.pending.original_filename)
    return None  # not_pending
