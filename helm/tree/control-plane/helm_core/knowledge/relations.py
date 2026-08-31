"""knowledge_relations, слой 1 — детерминированный, без LLM (P8.5.6, E13,
решение владельца 31.08.2026).

Ровно два источника, оба явные — ничего не додумывается сверх того, что
владелец сам написал в заметке:

  - `[[wikilink]]` в теле заметки -> `relation_type="relates_to"` ВСЕГДА,
    `evidence_type="explicit_link"`. Сам факт `[[A]] → [[B]]` не означает
    causes/supports/contradicts и т.п. — додумывать тип связи запрещено.
  - Явный список `relations:` в YAML-frontmatter заметки -> `relation_type`
    берётся дословно из поля `type`; без `type` запись целиком
    пропускается (та же дисциплина: не додумывать), `evidence_type=
    "explicit"`.

Frontmatter разбирается вручную построчным сканированием, БЕЗ PyYAML —
тот же принцип, что уже применён к `worker.py::_frontmatter()` (сборка
L1 SOURCE frontmatter): pyyaml — зависимость `helm-core`, но НЕ воркера
(`Dockerfile.worker` намеренно её не ставит), а именно воркер
(`process_job()`) — основной вызывающий этого модуля на реальных файлах.
Формат relations-блока — фиксированный, тривиальный, полноценный YAML-
парсер ради него не нужен.

Инференс-слой (Ollama, `evidence_type="inferred"`, с `confidence`) —
отдельный, более поздний шаг (P8.5.6 фаза 2 в `ollama_relations.py`),
сюда не входит и с этим не смешивается: разные evidence_type в одной
таблице, инференс никогда не подменяет и не удаляет explicit/
explicit_link записи.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import KnowledgeRelation

_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_FRONTMATTER_BLOCK = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_RELATIONS_HEADER = re.compile(r"^relations:\s*$")
_RELATION_TO_LINE = re.compile(r"^\s*-\s*to:\s*(.+?)\s*$")
_RELATION_TYPE_LINE = re.compile(r"^\s*type:\s*(.+?)\s*$")


@dataclass(frozen=True)
class ExtractedRelation:
    to_id: str
    relation_type: str
    evidence_type: str


def _strip_quotes_and_link(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    link = _WIKILINK.match(value)
    if link:
        value = link.group(1).strip()
    return value


def extract_wikilinks(text: str) -> list[ExtractedRelation]:
    """`[[Target]]`/`[[Target|Alias]]` в теле заметки — порядок появления,
    без дублей одного и того же target в одном тексте."""
    targets = dict.fromkeys(m.group(1).strip() for m in _WIKILINK.finditer(text))
    return [ExtractedRelation(to_id=t, relation_type="relates_to", evidence_type="explicit_link")
            for t in targets if t]


def extract_frontmatter_relations(text: str) -> list[ExtractedRelation]:
    """Ручной построчный разбор фиксированного формата:

        ---
        relations:
          - to: "Другая заметка"
            type: supports
          - to: "[[Третья заметка]]"
            type: contradicts
        ---

    Запись без `to` или без `type` пропускается целиком — недописанная
    связь не становится relates_to по умолчанию, это отдельная YAML-
    семантика от вольного текста заметки."""
    m = _FRONTMATTER_BLOCK.match(text)
    if not m:
        return []
    lines = m.group(1).splitlines()

    header_idx = next((i for i, line in enumerate(lines) if _RELATIONS_HEADER.match(line)), None)
    if header_idx is None:
        return []

    out: list[ExtractedRelation] = []
    pending_to: str | None = None
    for line in lines[header_idx + 1:]:
        if line and not line[0].isspace():
            break  # dedent — конец relations-блока, следующий top-level ключ
        to_match = _RELATION_TO_LINE.match(line)
        if to_match:
            pending_to = _strip_quotes_and_link(to_match.group(1))
            continue
        type_match = _RELATION_TYPE_LINE.match(line)
        if type_match and pending_to:
            rel_type = _strip_quotes_and_link(type_match.group(1))
            if rel_type:
                out.append(ExtractedRelation(to_id=pending_to, relation_type=rel_type,
                                             evidence_type="explicit"))
            pending_to = None
    return out


def _strip_frontmatter(text: str) -> str:
    m = _FRONTMATTER_BLOCK.match(text)
    return text[m.end():] if m else text


def extract_relations(text: str) -> list[ExtractedRelation]:
    """Frontmatter разбирается отдельно от тела: если `to:` в YAML сам
    записан как `[[Target]]`, это ОДНА explicit-связь, не ещё и вторая
    explicit_link — иначе один и тот же edge задваивался бы с двумя
    evidence_type за один авторский жест."""
    return extract_frontmatter_relations(text) + extract_wikilinks(_strip_frontmatter(text))


def note_id_for(*, original_filename: str | None, source_id: uuid.UUID) -> str:
    """Стабильный идентификатор заметки для `from_id`/резолва wikilink-
    target — basename без расширения, тот же принцип, по которому Obsidian
    резолвит `[[Target]]` по имени файла, не по полному пути. Без имени
    файла (`ingest_text()` без `original_filename`) — id source'а."""
    if original_filename:
        return Path(original_filename).stem
    return str(source_id)


def store_relations(session: Session, *, knowledge_user_id: uuid.UUID | None,
                    from_id: str, source_id: uuid.UUID, text: str) -> int:
    """Извлечь и записать relations слоя 1 для одной заметки. Идемпотентно
    на повторный ingest того же source: старые relations с тем же
    (knowledge_user_id, from_id, source_id) удаляются перед вставкой, чтобы
    повторный разбор (например, после фикса парсера) не копил дубли."""
    relations = extract_relations(text)

    session.query(KnowledgeRelation).filter(
        KnowledgeRelation.knowledge_user_id == knowledge_user_id,
        KnowledgeRelation.from_id == from_id,
        KnowledgeRelation.source_id == source_id,
    ).delete(synchronize_session=False)

    for r in relations:
        session.add(KnowledgeRelation(
            knowledge_user_id=knowledge_user_id, from_id=from_id, to_id=r.to_id,
            relation_type=r.relation_type, evidence_type=r.evidence_type,
            source_id=source_id,
        ))
    return len(relations)
