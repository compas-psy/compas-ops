"""v3.8 §14.15 — вернуть оригинал документа, а не пересказ.

Спека формулирует это как отдельный тип запроса:

    user-scoped search → exact source → owner/access/status check
    → RAW original bytes

Ключевое слово — **original bytes**. Не «восстановленный текст», не
«разобранный Markdown», а ровно тот файл, который человек прислал, с тем
же SHA256. Именно поэтому здесь нет ни парсера, ни модели: восстановить
байты нечем и не из чего, их можно только отдать.

Три правила доступа из §14.15:

- заархивированное и вытеснённое новой версией **можно скачать
  владельцу для разбора**, но нельзя использовать в обычных ответах —
  это разные вопросы, и здесь разрешено то, что запрещено в `probe()`;
- удалённое недоступно из живого хранилища. Отдельного статуса
  «удалён» у источника в этой схеме нет вовсе: строки исчезают только
  вместе с удалением аккаунта (`offboarding.py`), поэтому «удалённое
  недоступно» выполняется тем, что искать уже нечего. Названо здесь
  прямо, чтобы следующий читатель не искал несуществующий статус;
- «sensitive download follows passkey rules» — см. `api/panel.py`, где
  скачивание требует свежего passkey.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .health_schema import (
    health_schema_configured, health_session, is_health_domain, read_original_filename,
)
from .recall import build_or_tsquery
from .tenancy import bind_knowledge_user
from ..models import (
    HealthKnowledgeChunk, HealthKnowledgeSourcePrivate, KnowledgeChunk, KnowledgeSource,
    KnowledgeStatus,
)

#: Больше — и «список кандидатов» перестаёт быть списком, который можно
#: прочитать глазами в чате.
MAX_CANDIDATES = 5

#: §14.15: «Sensitive download follows passkey/bot owner rules». Клиентский
#: контент — единственная категория, которую спека называет отдельно
#: (§14.15 «not indexed into general namespaces»).
SENSITIVE_LEVELS = ("client_restricted",)


@dataclass
class SourceCandidate:
    source_id: str
    original_filename: str | None
    domain: str
    status: str
    sensitivity: str
    sha256: str
    created_at: str


def _display_filename(source: KnowledgeSource) -> str | None:
    """ADR-005/P12: `source.original_filename` — `None` для health, если
    health-схема настроена (см. `ingest.py::_public_original_filename()`).
    Вызывающий код здесь — `find_sources()`/`read_original()`, оба уже за
    passkey-гейтом ЭТОГО ЖЕ владельца (`api/panel.py`) — то же законное
    same-user disclosure, что и `worker.py::_notify_owner_of_result()`,
    не раскрытие постороннему helm_app/chief."""
    if is_health_domain(source.domain) and health_schema_configured():
        return read_original_filename(source_id=source.id, knowledge_user_id=source.knowledge_user_id)
    return source.original_filename


def _as_candidate(source: KnowledgeSource) -> SourceCandidate:
    return SourceCandidate(
        source_id=str(source.id), original_filename=_display_filename(source),
        domain=source.domain, status=source.status, sensitivity=source.sensitivity,
        sha256=source.sha256, created_at=source.created_at.isoformat(),
    )


def _health_candidates_by_name(session: Session, *, tenant_id: uuid.UUID, pattern: str,
                               limit: int) -> list[KnowledgeSource]:
    """`public.knowledge_sources.original_filename` — `None` для health,
    ILIKE в `find_sources()` его не видит вовсе. Без этой ветки владелец
    не смог бы найти `source_id` собственного health-документа по имени,
    чтобы затем скачать его через `read_original()` (тот же passkey-гейт,
    что и у этого поиска) — регрессия, которую вносит сама ADR-005/P12
    маршрутизация, не отдельная функция."""
    if not health_schema_configured() or limit <= 0:
        return []
    with health_session(tenant_id) as hsession:
        ids = hsession.scalars(
            select(HealthKnowledgeSourcePrivate.source_id)
            .where(HealthKnowledgeSourcePrivate.knowledge_user_id == tenant_id,
                  HealthKnowledgeSourcePrivate.original_filename.ilike(pattern))
            .order_by(HealthKnowledgeSourcePrivate.created_at.desc())
            .limit(limit)
        ).all()
    if not ids:
        return []
    sources = session.scalars(
        select(KnowledgeSource).where(KnowledgeSource.id.in_(ids),
                                      KnowledgeSource.knowledge_user_id == tenant_id)
    ).all()
    by_id = {s.id: s for s in sources}
    return [by_id[i] for i in ids if i in by_id]


def _health_candidates_by_content(session: Session, *, tenant_id: uuid.UUID, query: str,
                                  limit: int) -> list[SourceCandidate]:
    """Тот же fallback, что `_health_candidates_by_name()`, но для
    контент-ветки `find_sources()` — чанки health-домена живут в
    `health.knowledge_chunks`, обычный join на `public.knowledge_chunks`
    их не находит."""
    if not health_schema_configured() or limit <= 0:
        return []
    tsquery = build_or_tsquery(query)
    rank = func.max(func.ts_rank(HealthKnowledgeChunk.tsv, tsquery, 2)).label("rank")
    with health_session(tenant_id) as hsession:
        rows = hsession.execute(
            select(HealthKnowledgeChunk.source_id, rank)
            .where(HealthKnowledgeChunk.knowledge_user_id == tenant_id,
                  HealthKnowledgeChunk.tsv.op("@@")(tsquery))
            .group_by(HealthKnowledgeChunk.source_id)
            .order_by(rank.desc()).limit(limit)
        ).all()
    source_ids = [sid for sid, _ in rows]
    if not source_ids:
        return []
    sources = session.scalars(
        select(KnowledgeSource).where(KnowledgeSource.id.in_(source_ids),
                                      KnowledgeSource.knowledge_user_id == tenant_id)
    ).all()
    by_id = {s.id: s for s in sources}
    return [_as_candidate(by_id[sid]) for sid in source_ids if sid in by_id]


def find_sources(session: Session, *, query: str, knowledge_user_id: uuid.UUID | None = None,
                 limit: int = MAX_CANDIDATES) -> list[SourceCandidate]:
    """Найти свои документы по имени файла или по содержимому.

    Сначала имя: люди просят файл именно так — «пришли договор с
    подрядчиком». Совпадение по имени точнее любого поиска по тексту и
    не зависит от того, разобрался ли документ вообще. Если по имени
    ничего — ищем по разобранному тексту.

    Заархивированное и отключённое из выдачи НЕ исключается: §14.15
    прямо разрешает скачивать такое владельцу для разбора. Исключается
    только удалённое.
    """
    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    base = (select(KnowledgeSource)
            .where(KnowledgeSource.knowledge_user_id == tenant_id))

    pattern = f"%{query.strip()}%"
    by_name = list(session.scalars(
        base.where(KnowledgeSource.original_filename.ilike(pattern))
        .order_by(KnowledgeSource.created_at.desc()).limit(limit)
    ).all())
    by_name += _health_candidates_by_name(session, tenant_id=tenant_id, pattern=pattern,
                                          limit=limit - len(by_name))
    if by_name:
        return [_as_candidate(s) for s in by_name[:limit]]

    tsquery = build_or_tsquery(query)
    rank = func.max(func.ts_rank(KnowledgeChunk.tsv, tsquery, 2)).label("rank")
    rows = session.execute(
        select(KnowledgeSource, rank)
        .join(KnowledgeChunk, KnowledgeChunk.source_id == KnowledgeSource.id)
        .where(KnowledgeSource.knowledge_user_id == tenant_id)
        .where(KnowledgeChunk.tsv.op("@@")(tsquery))
        .group_by(KnowledgeSource.id)
        .order_by(rank.desc())
        .limit(limit)
    ).all()
    candidates = [_as_candidate(source) for source, _ in rows]
    candidates += _health_candidates_by_content(session, tenant_id=tenant_id, query=query,
                                                limit=limit - len(candidates))
    return candidates


@dataclass
class OriginalBytes:
    filename: str
    media_type: str
    data: bytes
    sha256: str
    #: True, если файл заархивирован/отключён: скачать можно, но в
    #: обычных ответах он не участвует (§14.15).
    review_only: bool


class DocumentUnavailable(Exception):
    """Документ есть в базе, но выдать байты нельзя — и объяснимо почему."""


def read_original(session: Session, source_id: uuid.UUID, *,
                  knowledge_user_id: uuid.UUID | None = None) -> OriginalBytes:
    """Отдать исходные байты. Проверки — в порядке §14.15.

    Владение проверяется дважды: явным предикатом здесь и запретом на
    уровне самой базы. Промах любого из двух слоёв даёт «не найдено», а
    не чужой файл.
    """
    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    source = session.scalar(
        select(KnowledgeSource).where(KnowledgeSource.id == source_id,
                                      KnowledgeSource.knowledge_user_id == tenant_id))
    if source is None:
        # Ровно то же сообщение, что и для чужого документа: существование
        # чужого файла — тоже сведения о нём. Сюда же попадает удалённый
        # аккаунт: его строк больше нет, и «недоступно из живого
        # хранилища» выполняется само.
        raise DocumentUnavailable("документ не найден")

    path = Path(source.raw_path)
    if not path.is_file():
        # `ingest_text()` создаёт источник из готового текста и файла на
        # диск не пишет вовсе — у такого источника оригинала физически
        # нет. Честнее сказать это, чем отдать разобранный текст под
        # видом оригинала.
        raise DocumentUnavailable(
            "у этой записи нет исходного файла — она заведена из текста, "
            "а не из документа")

    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != source.sha256:
        # Файл на диске разошёлся с тем, что записано в базе. Это не
        # «немного не то» — это потеря доказуемости происхождения (§14.1
        # RAW immutable), и отдавать такие байты как оригинал нельзя.
        raise DocumentUnavailable(
            "файл на диске не совпадает с записанной контрольной суммой")

    return OriginalBytes(
        filename=_display_filename(source) or f"{source.sha256[:12]}.bin",
        media_type=source.mime_type or "application/octet-stream",
        data=data, sha256=digest,
        review_only=source.status in (KnowledgeStatus.ARCHIVED, KnowledgeStatus.SUPERSEDED),
    )


def is_sensitive(session: Session, source_id: uuid.UUID, *,
                 knowledge_user_id: uuid.UUID | None = None) -> bool:
    tenant_id = bind_knowledge_user(session, knowledge_user_id)
    level = session.scalar(
        select(KnowledgeSource.sensitivity)
        .where(KnowledgeSource.id == source_id,
               KnowledgeSource.knowledge_user_id == tenant_id))
    return level in SENSITIVE_LEVELS
