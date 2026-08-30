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
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import KnowledgeChunk, KnowledgeIngestJob, KnowledgeIngestStatus, KnowledgeSource, KnowledgeStatus

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


def ingest_text(session: Session, *, domain: str, text: str,
                original_filename: str | None = None,
                sensitivity: str = "internal", trust: str = "extracted",
                vault_root: str = DEFAULT_VAULT_ROOT) -> KnowledgeSource:
    """Сохранить текст как source + лексически проиндексированные чанки.

    Повторный вызов с тем же текстом возвращает уже существующий source,
    не создаёт дубль (SHA256-дедуп, §14.5).
    """
    sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = session.scalar(select(KnowledgeSource).where(KnowledgeSource.sha256 == sha256))
    if existing is not None:
        return existing

    source = KnowledgeSource(
        domain=domain, sha256=sha256,
        raw_path=f"{vault_root}/raw/{domain}/{sha256}.txt",
        source_path=f"{vault_root}/sources/{sha256}.md",
        original_filename=original_filename, mime_type="text/plain", parser="manual",
        sensitivity=sensitivity, trust=trust, status=KnowledgeStatus.ACTIVE,
    )
    session.add(source)
    session.flush()

    for ordinal, chunk_text in enumerate(split_chunks(text)):
        session.add(KnowledgeChunk(
            source_id=source.id, ordinal=ordinal, text=chunk_text,
            # to_tsvector на стороне БД, не Python: русская конфигурация
            # словаря живёт в Postgres, дублировать её логику в приложении
            # означало бы гарантированное расхождение при следующем апдейте.
            tsv=func.to_tsvector("russian", chunk_text),
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
                             vault_root: str = DEFAULT_VAULT_ROOT) -> RegisterFileResult:
    """Зарегистрировать файл, уже лежащий на диске, для асинхронного парсинга.

    Быстрая синхронная часть pipeline'а (§14.5.1: "immediate
    acknowledgement") — читает файл только чтобы посчитать SHA256, сам
    парсинг не запускает. Дедуп: повторный файл с тем же содержимым
    возвращает существующий source без нового ingest job.
    """
    data = raw_path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    existing = session.scalar(select(KnowledgeSource).where(KnowledgeSource.sha256 == sha256))
    if existing is not None:
        return RegisterFileResult(source=existing, job=None, created=False)

    source = KnowledgeSource(
        domain=domain, sha256=sha256, raw_path=str(raw_path),
        source_path=f"{vault_root}/sources/{sha256}.md",
        original_filename=original_filename, mime_type=mime_type, parser=None,
        sensitivity=sensitivity, trust=trust, status=KnowledgeStatus.ACTIVE,
    )
    session.add(source)
    session.flush()

    job = KnowledgeIngestJob(source_id=source.id, channel=channel, recipient=recipient,
                             status=KnowledgeIngestStatus.PENDING)
    session.add(job)
    session.flush()
    return RegisterFileResult(source=source, job=job, created=True)
