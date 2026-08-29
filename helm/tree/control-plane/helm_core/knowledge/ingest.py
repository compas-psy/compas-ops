"""Минимальный ingest текста в HELM Knowledge (ТЗ §14.5).

НЕ полный pipeline из спеки. MarkItDown/Docling/GigaAM, парсинг реальных
файлов, запись RAW на диск и quality gate ждут P8.5.2/P8.5.3
(V3.4-DELTA.md, "needs additive change" → отложено) — они требуют
установки пакетов и бенчмарка на живом сервере, не делаются офлайн.

Здесь — только то, что нужно, чтобы Knowledge Probe (§14.11) уже сейчас
могло что-то находить: сохранить текст с provenance-метаданными и
разбить на чанки для лексического поиска (§14.9). Единственное правило
полного pipeline, которое здесь всё же соблюдается: дедуп по SHA256
(§14.5 — «Повторный файл с тем же SHA256 не обрабатывается заново, а
связывается с существующим source»), потому что оно не зависит от
парсеров вообще.

`raw_path`/`source_path` записываются как ожидаемое расположение файла
(куда его положит настоящий pipeline), а не как файл, реально записанный
на диск, — запись RAW на диск и есть P8.5.2.
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import KnowledgeChunk, KnowledgeSource, KnowledgeStatus

#: Разбиение по абзацам — не структурные чанки Docling (с учётом таблиц и
#: страниц), но детерминированно и достаточно для FTS уже сейчас. Меняется
#: вместе с P8.5.2, не раньше — переписывать дважды смысла нет.
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")


def _split_chunks(text: str) -> list[str]:
    parts = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
    return parts or [text.strip()]


def ingest_text(session: Session, *, domain: str, text: str,
                original_filename: str | None = None,
                sensitivity: str = "internal", trust: str = "extracted") -> KnowledgeSource:
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
        raw_path=f"/opt/helm-knowledge/raw/{domain}/{sha256}.txt",
        source_path=f"/opt/helm-knowledge/sources/{sha256}.md",
        original_filename=original_filename, mime_type="text/plain", parser="manual",
        sensitivity=sensitivity, trust=trust, status=KnowledgeStatus.ACTIVE,
    )
    session.add(source)
    session.flush()

    for ordinal, chunk_text in enumerate(_split_chunks(text)):
        session.add(KnowledgeChunk(
            source_id=source.id, ordinal=ordinal, text=chunk_text,
            # to_tsvector на стороне БД, не Python: русская конфигурация
            # словаря живёт в Postgres, дублировать её логику в приложении
            # означало бы гарантированное расхождение при следующем апдейте.
            tsv=func.to_tsvector("russian", chunk_text),
        ))
    return source
