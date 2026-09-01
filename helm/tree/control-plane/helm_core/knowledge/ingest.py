"""Ingest в HELM Knowledge (ТЗ §14.5) — два пути.

`ingest_text()` — минимальный путь для готового текста, без файла на
диске: сохранить текст с provenance-метаданными и разбить на чанки для
лексического поиска (§14.9). `raw_path`/`source_path` здесь — ожидаемое
расположение, не файл, реально записанный на диск.

`register_file_for_ingest()` — реальный путь для файла, УЖЕ лежащего на
диске (P8.5.2): синхронная "ack" часть pipeline'а — SHA256, создание
`knowledge_sources` + `knowledge_ingest_jobs` (status=PENDING), без
самого парсинга. Парсинг — асинхронный, в отдельном процессе
(`worker.py::process_job`), чтобы тяжёлый Docling-разбор не держал
открытым запрос от Telegram/MAX (§14.5.1: "must not hold the request
open"). Доставка файла ОТ Telegram/MAX В `/opt/helm-knowledge/raw/` —
spool, atomic move — отдельная, ещё не реализованная задача (P8.5.7);
эта функция принимает уже готовый путь, откуда бы он ни взялся.

Общее для обоих путей: дедуп по SHA256 (§14.5 — «повторный файл с тем
же SHA256 не обрабатывается заново, связывается с существующим
source») — единственное правило полного pipeline, не зависящее от
парсеров вообще.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import KnowledgeChunk, KnowledgeIngestJob, KnowledgeIngestStatus, KnowledgeSource, KnowledgeStatus
from .atomizer import atomize_and_store
from .embeddings import embed_texts_or_none
from .health_schema import health_schema_configured, is_health_domain, write_chunks, write_original_filename
from .quotas import check_and_record_ingest, check_queue_depth, record_entry_formed
from .relations import note_id_for, store_relations
from .tenancy import bind_knowledge_user

#: Корень Vault (§14.2). Параметр, а не только константа: тесты обязаны
#: указывать свой временный каталог — писать в /opt/helm-knowledge при
#: запуске pytest на произвольной машине было бы и неверно, и опасно.
DEFAULT_VAULT_ROOT = "/opt/helm-knowledge"

#: Разбиение по абзацам — не структурные чанки Docling (с учётом таблиц и
#: страниц), но детерминированно и достаточно для FTS уже сейчас. Меняется
#: вместе с P8.5.2, не раньше — переписывать дважды смысла нет.
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")


def split_chunks(text: str) -> list[str]:
    """Публичная: переиспользуется `worker.py` при индексации реально
    распарсенных файлов (тот же контракт разбиения, что и у ingest_text())."""
    parts = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
    return parts or [text.strip()]


def _public_original_filename(*, domain: str, original_filename: str | None,
                              source_id: uuid.UUID, knowledge_user_id: uuid.UUID) -> str | None:
    """ADR-005/P12: для `health`, если health-схема настроена, реальное
    имя файла уходит в `health.knowledge_source_private` (единственное
    чувствительное поле, см. докстринг `HealthKnowledgeSourcePrivate`),
    а в `public.knowledge_sources` остаётся `None`. Если health-схема ещё
    не настроена (`scripts/setup-health-role.sh` не прогнан на этом
    сервере) — деградация на прежнее поведение: имя остаётся в `public`
    как у любого другого домена, не падаем и не теряем данные."""
    if not is_health_domain(domain) or not health_schema_configured():
        return original_filename
    write_original_filename(source_id=source_id, knowledge_user_id=knowledge_user_id,
                            original_filename=original_filename)
    return None


def ingest_text(session: Session, *, domain: str, text: str,
                original_filename: str | None = None,
                sensitivity: str = "internal", trust: str = "extracted",
                vault_root: str | None = None,
                knowledge_user_id: uuid.UUID | None = None) -> KnowledgeSource:
    """Сохранить текст как source + лексически проиндексированные чанки.

    Повторный вызов с тем же текстом ОТ ТОГО ЖЕ knowledge_user_id
    возвращает уже существующий source, не создаёт дубль (SHA256-дедуп,
    §14.5) — дедуп per-tenant (v3.8 §14.4: идентичные байты у разных
    пользователей НЕ схлопываются в одну запись).

    `knowledge_user_id=None` — существующие call sites (P8.6.2 Dedicated
    Knowledge Bot ещё не существует): разрешается в SYSTEM_OWNER.
    """
    # `None` + резолв здесь, не литеральный default в сигнатуре — тот же
    # приём, что уже применён в memory.py/offboarding.py: default-значение
    # именованного параметра вычисляется ОДИН РАЗ при определении функции,
    # подмена `ingest.DEFAULT_VAULT_ROOT` тестовой фикстурой
    # (`_never_touch_the_real_vault`) на уже связанный default не подействует.
    # Раньше это было безобидно (ingest_text() ничего не писал на диск), но
    # ADR-019 (atomize_and_store() ниже) пишет .md-файлы атомов — без этой
    # правки тесты без явного vault_root писали бы в настоящий Vault.
    vault_root = vault_root or DEFAULT_VAULT_ROOT
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)

    sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.knowledge_user_id == knowledge_user_id,
            KnowledgeSource.sha256 == sha256,
        )
    )
    if existing is not None:
        return existing

    source = KnowledgeSource(
        knowledge_user_id=knowledge_user_id, domain=domain, sha256=sha256,
        raw_path=f"{vault_root}/raw/{domain}/{sha256}.txt",
        source_path=f"{vault_root}/sources/{sha256}.md",
        original_filename=original_filename, mime_type="text/plain", parser="manual",
        sensitivity=sensitivity, trust=trust, status=KnowledgeStatus.ACTIVE,
    )
    session.add(source)
    session.flush()  # source.id нужен ДО вызова ниже — sidecar ссылается на него по значению, не по FK.

    # ADR-005/P12: та же маршрутизация, что у register_file_for_ingest().
    source.original_filename = _public_original_filename(
        domain=domain, original_filename=original_filename,
        source_id=source.id, knowledge_user_id=knowledge_user_id)

    record_entry_formed(session, knowledge_user_id=knowledge_user_id, sources=1)

    # P8.5.6 слой 1 (E13, решение владельца 31.08.2026): [[wikilink]] +
    # явный YAML relations: — детерминированно, до любого Graphify.
    # ADR-005/P12: note_id_for() получает исходный original_filename (не
    # `source.original_filename`, уже перезаписанный выше на None для
    # health) — from_id для wikilink-резолва нужен независимо от того,
    # куда физически уехала сама запись relation.
    store_relations(session, domain=domain, knowledge_user_id=knowledge_user_id,
                    from_id=note_id_for(original_filename=original_filename, source_id=source.id),
                    source_id=source.id, text=text)

    # ADR-019: L2 semantic atomizer — поверх уже сделанного store_relations()
    # выше, аддитивно (fail-open: недоступность атомизатора не мешает
    # созданию source/chunks/слоя-1-relations, см. atomizer.py).
    atomize_and_store(session, domain=domain, knowledge_user_id=knowledge_user_id,
                      source_id=source.id, source_sha256=sha256, text=text,
                      vault_root=vault_root)

    chunks = split_chunks(text)
    # ADR-025: недоступность embed-сервиса не должна мешать созданию
    # source/чанков — embed_texts_or_none() откатывается на None на чанк,
    # лексический поиск (tsv) по нему продолжает работать как раньше.
    embeddings = embed_texts_or_none(chunks)
    if is_health_domain(domain) and health_schema_configured():
        # ADR-005/P12: текст чанка — самое чувствительное поле source'а,
        # уходит в health.knowledge_chunks вместо public.knowledge_chunks.
        write_chunks(source_id=source.id, knowledge_user_id=knowledge_user_id,
                    chunks=chunks, embeddings=embeddings)
    else:
        for ordinal, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            session.add(KnowledgeChunk(
                knowledge_user_id=knowledge_user_id, source_id=source.id, ordinal=ordinal,
                text=chunk_text,
                # to_tsvector на стороне БД, не Python: русская конфигурация
                # словаря живёт в Postgres, дублировать её логику в приложении
                # означало бы гарантированное расхождение при следующем апдейте.
                tsv=func.to_tsvector("russian", chunk_text),
                embedding=embedding,
            ))
    return source


@dataclass
class RegisterFileResult:
    source: KnowledgeSource
    #: None означает «уже проиндексирован раньше» (SHA256-дедуп) — новой
    #: работы для воркера нет, job не создаётся.
    job: KnowledgeIngestJob | None
    created: bool


def register_file_for_ingest(session: Session, *, domain: str, raw_path: Path,
                             original_filename: str | None = None,
                             mime_type: str | None = None,
                             sensitivity: str = "internal", trust: str = "extracted",
                             channel: str | None = None, recipient: str | None = None,
                             vault_root: str | None = None,
                             knowledge_user_id: uuid.UUID | None = None) -> RegisterFileResult:
    """Зарегистрировать файл, уже лежащий на диске, для асинхронного парсинга.

    Быстрая синхронная часть pipeline'а (§14.5.1: "immediate
    acknowledgement") — читает файл только чтобы посчитать SHA256, сам
    парсинг не запускает. Дедуп: повторный файл с тем же содержимым ОТ
    ТОГО ЖЕ knowledge_user_id возвращает существующий source без нового
    ingest job — per-tenant (v3.8 §14.4), не глобальный SHA256.

    `knowledge_user_id=None` — существующие call sites (P8.6.2 Dedicated
    Knowledge Bot ещё не существует): разрешается в SYSTEM_OWNER.
    """
    # См. ingest_text() выше — тот же приём и та же причина.
    vault_root = vault_root or DEFAULT_VAULT_ROOT
    knowledge_user_id = bind_knowledge_user(session, knowledge_user_id)

    data = raw_path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    existing = session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.knowledge_user_id == knowledge_user_id,
            KnowledgeSource.sha256 == sha256,
        )
    )
    if existing is not None:
        return RegisterFileResult(source=existing, job=None, created=False)

    # §14.4 "oversized user upload rejected before resource exhaustion" —
    # ДО записи source, не постфактум; дубликаты (проверка выше) не
    # тарифицируются повторно. check_queue_depth() здесь же покрывает и
    # ZIP-члены (каждый идёт через эту же функцию, batch_intake.py::
    # _process_item()) — при переполнении очереди дальнейшие члены
    # батча просто получают FAILED/retryable, не рушат уже принятые.
    check_and_record_ingest(session, knowledge_user_id=knowledge_user_id, size_bytes=len(data))
    check_queue_depth(session, knowledge_user_id=knowledge_user_id)

    source = KnowledgeSource(
        knowledge_user_id=knowledge_user_id, domain=domain, sha256=sha256, raw_path=str(raw_path),
        source_path=f"{vault_root}/sources/{sha256}.md",
        original_filename=original_filename, mime_type=mime_type, parser=None,
        sensitivity=sensitivity, trust=trust, status=KnowledgeStatus.ACTIVE,
    )
    session.add(source)
    session.flush()  # source.id нужен ДО вызова ниже — sidecar ссылается на него по значению, не по FK.

    # ADR-005/P12: для health реальное имя файла уезжает в health-схему,
    # public.knowledge_sources.original_filename перезаписывается на None.
    source.original_filename = _public_original_filename(
        domain=domain, original_filename=original_filename,
        source_id=source.id, knowledge_user_id=knowledge_user_id)

    record_entry_formed(session, knowledge_user_id=knowledge_user_id, sources=1)

    job = KnowledgeIngestJob(knowledge_user_id=knowledge_user_id, source_id=source.id,
                             channel=channel, recipient=recipient,
                             status=KnowledgeIngestStatus.PENDING)
    session.add(job)
    session.flush()
    return RegisterFileResult(source=source, job=job, created=True)
