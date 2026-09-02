"""v4.0 §14.4/§14.5/§14.20 — единственный путь, которым граф попадает в базу.

Инвариант, поставленный владельцем первым требованием R3:

    semantic run → проверка → READY → атомарное переключение текущей

Ничто не пишет узлы «просто так». Каждый узел, каждое ребро и каждое
упоминание принадлежат прогону; пока прогон не дошёл до READY, его
содержимое существует, но не является текущим ни для одного источника, и
запросы его не видят (§14.5: «Queries must never observe half-written
staging nodes»). Переключение — один UPDATE, и база его же и проверяет
триггером из R2-hardening.

Отсюда следует то, чего не было в semantic-v1: неудачный разбор ничего
не портит. Прежняя ревизия остаётся текущей, пока новая не доказала, что
годится (§14.20: «Never destroy last known-good semantic graph before
replacement passes»).

Health. Прогон живёт в public (в нём нет содержимого источника), сам граф
— в health-схеме, отдельным соединением и отдельной ролью. Это две
транзакции, и по-другому быть не может: `helm_health` не имеет прав на
public. Опасности это не создаёт: единственное, что делает граф видимым,
— переключение указателя в public, и оно идёт ПОСЛЕ успешной записи
графа. Оборвись процесс между ними — останется невидимая ревизия, а не
испорченный ответ.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from .health_schema import health_schema_configured, health_session, is_health_domain
from .semantic_extract import (
    DEFAULT_MODEL, ExtractionFailed, MAX_ATOMS_PER_WINDOW, WindowExtraction, WindowTruncated,
    extract_window,
)
from .semantic_windows import SemanticWindow, build_windows, split_window
from ..models import (
    HealthKnowledgeEdge, HealthKnowledgeEntityAlias, HealthKnowledgeNode,
    HealthKnowledgeNodeMention, HealthKnowledgeSemanticWindow,
    KnowledgeEdge, KnowledgeEntityAlias, KnowledgeNode, KnowledgeNodeMention,
    KnowledgeSemanticRun, KnowledgeSemanticWindow, KnowledgeSource,
)
from ..models.base import (
    SemanticDatePrecision, SemanticEvidenceType, SemanticNodeKind, SemanticRunStatus,
    SemanticWindowStatus,
)

logger = logging.getLogger(__name__)

#: Сколько раз подряд можно делить переполненное окно. Три уровня — это
#: восемь кусков из одного окна; если и они переполняются, дело не в
#: размере, а в тексте (список из тысячи строк), и честный ответ —
#: FAILED, а не бесконечное деление.
MAX_SPLIT_DEPTH = 3

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class _Models:
    """Одна и та же запись в public или в health.

    Поля у зеркал названы одинаково намеренно (см. `health_tables.py`),
    поэтому весь цикл публикации ниже написан один раз, а не дважды.
    """

    node: type
    mention: type
    edge: type
    alias: type
    window: type


PUBLIC_MODELS = _Models(KnowledgeNode, KnowledgeNodeMention, KnowledgeEdge,
                        KnowledgeEntityAlias, KnowledgeSemanticWindow)
HEALTH_MODELS = _Models(HealthKnowledgeNode, HealthKnowledgeNodeMention, HealthKnowledgeEdge,
                        HealthKnowledgeEntityAlias, HealthKnowledgeSemanticWindow)


@dataclass
class PublishResult:
    run_id: uuid.UUID
    status: str
    switched: bool
    windows_total: int = 0
    windows_processed: int = 0
    windows_failed: int = 0
    nodes_created: int = 0
    edges_created: int = 0
    coverage_ratio: float = 0.0


def normalize_key(label: str) -> str:
    """Ключ для сопоставления сущностей (§14.7).

    Только регистр и пробелы — ничего умнее. Умное сопоставление здесь
    было бы автослиянием по похожести, а §14.7 разрешает автослияние
    ТОЛЬКО при совпадении сильной личности; разрешение сущностей — шаг
    R6, и делать его тайком внутри записи нельзя.
    """
    return _WHITESPACE.sub(" ", label.strip()).casefold()


def parse_occurred_at(value: str | None, precision: str | None
                      ) -> tuple[datetime | None, str | None]:
    """Структурная дата из строки модели (§14.8).

    Неразобранная дата даёт `(None, unknown)`, а не выдуманное число:
    §14.8 требует различать «в августе» и «19.08.2026» и запрещает
    придумывать точность. Текстовая подсказка при этом не теряется —
    она остаётся в тексте атома.
    """
    if not value:
        return None, precision or None
    raw = value.strip()
    for fmt, matched in (("%Y-%m-%d", SemanticDatePrecision.DAY),
                         ("%Y-%m", SemanticDatePrecision.MONTH),
                         ("%Y", SemanticDatePrecision.YEAR)):
        try:
            parsed = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return parsed, (precision or matched.value)
    return None, SemanticDatePrecision.UNKNOWN.value


def _window_row(models: _Models, *, window: SemanticWindow, run_id: uuid.UUID,
                source_id: uuid.UUID, tenant_id: uuid.UUID, ordinal: int,
                parent_id: uuid.UUID | None):
    return models.window(
        knowledge_user_id=tenant_id, semantic_run_id=run_id, source_id=source_id,
        ordinal=ordinal, parent_window_id=parent_id,
        char_start=window.char_start, char_end=window.char_end,
        heading_path=" → ".join(window.heading_path) or None,
        text_hash=window.text_hash, status=SemanticWindowStatus.PENDING,
    )


def _result_hash(extraction: WindowExtraction) -> str:
    """Отпечаток результата окна (§14.4.1: «stores a result hash/count
    even when it produced zero nodes»). Считается по разобранным полям,
    а не по сырому ответу: два ответа, отличающиеся пробелами, — один и
    тот же результат."""
    payload = {
        "entities": sorted((e.entity_type, e.label) for e in extraction.entities),
        "atoms": sorted((a.kind, a.title, a.text) for a in extraction.atoms),
        "edges": sorted((e.from_local_id, e.relation_type, e.to_local_id)
                        for e in extraction.edges),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _write_extraction(graph, models: _Models, *, extraction: WindowExtraction,
                      window: SemanticWindow, window_row, run_id: uuid.UUID,
                      source_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[int, int]:
    """Записать разбор одного окна. Возвращает (узлов, рёбер).

    Сущности этого прогона НЕ сливаются с сущностями прошлых прогонов и
    других источников. Сливать их здесь означало бы делать разрешение
    сущностей по совпадению подписи — ровно то, что §14.6 запрещает и
    что делал semantic-v1. Одно лицо в двух прогонах — задача R6, и она
    решается осознанным слиянием, а не побочным эффектом записи.
    """
    by_local: dict[str, uuid.UUID] = {}
    nodes = 0

    for entity in extraction.entities:
        # R3.1, найдено владельцем 02.09.2026: `subtype` здесь подменялся
        # `entity_type.lower()`, и настоящий subtype (`doctor`,
        # `medical_specialty`) терялся молча. `entity_type` — своё поле;
        # `statement_text` у ENTITY нет намеренно (§14.6, см. докстринг
        # модели) — складывать сюда прозу источника значило бы вернуть
        # растущую заметку semantic-v1.
        node = models.node(
            knowledge_user_id=tenant_id, kind=SemanticNodeKind.ENTITY,
            entity_type=entity.entity_type.lower() or None, subtype=entity.subtype,
            canonical_label=entity.label, normalized_key=normalize_key(entity.label),
            semantic_run_id=run_id,
        )
        graph.add(node)
        graph.flush()
        by_local[entity.local_id] = node.id
        nodes += 1
        for alias in entity.aliases:
            graph.add(models.alias(
                knowledge_user_id=tenant_id, entity_node_id=node.id, alias=alias,
                normalized_alias=normalize_key(alias), source_id=source_id))

    for atom in extraction.atoms:
        # R3.1: `atom.text` — законченное утверждение модели (§14.4.2) —
        # раньше не записывался вовсе; сохранялся только `atom.title` в
        # `canonical_label`. `entity_type` у утверждения нет: у него нет
        # личности, есть только текст и дата.
        occurred_at, precision = parse_occurred_at(atom.occurred_at, atom.date_precision)
        node = models.node(
            knowledge_user_id=tenant_id, kind=atom.kind, subtype=atom.subtype,
            canonical_label=atom.title, statement_text=atom.text,
            occurred_at_start=occurred_at, date_precision=precision, semantic_run_id=run_id,
        )
        graph.add(node)
        graph.flush()
        by_local[atom.local_id] = node.id
        nodes += 1

    # Упоминание на КАЖДЫЙ узел окна: §14.5 требует происхождения на
    # уровне источника, а не «где-то в этом документе». Без него ответ
    # нельзя проследить до места в тексте (§30.8.5 F).
    #
    # ДОЛГ, зафиксированный владельцем 02.09.2026 (R3.1, не блокирует R3):
    # диапазон здесь — границы ВСЕГО окна (до WINDOW_MAX_CHARS символов),
    # не точный фрагмент внутри него, где на самом деле стоит узел. Для
    # приёмки R5 (§30.8.5 F «точное происхождение») этого недостаточно —
    # нужен диапазон внутри окна, который модель пока не отдаёт. Разбор
    # результата от исходного текста уже дал бы точные смещения; здесь
    # сознательно не делается, чтобы не расширять R3.1 сверх найденных
    # владельцем двух потерь данных.
    for node_id in by_local.values():
        graph.add(models.mention(
            knowledge_user_id=tenant_id, node_id=node_id, source_id=source_id,
            window_id=window_row.ordinal, char_start=window.char_start,
            char_end=window.char_end, evidence_text_hash=window.text_hash,
            evidence_type=SemanticEvidenceType.EXTRACTED, semantic_run_id=run_id))

    edges = 0
    for edge in extraction.edges:
        graph.add(models.edge(
            knowledge_user_id=tenant_id,
            from_node_id=by_local[edge.from_local_id],
            to_node_id=by_local[edge.to_local_id],
            relation_type=edge.relation_type, role=edge.role, source_id=source_id,
            evidence_type=SemanticEvidenceType.EXTRACTED, semantic_run_id=run_id))
        edges += 1

    graph.flush()
    return nodes, edges


def _process(graph, models: _Models, *, window: SemanticWindow, ordinal: int,
             parent_id: uuid.UUID | None, depth: int, domain: str, run_id: uuid.UUID,
             source_id: uuid.UUID, tenant_id: uuid.UUID, extract, model: str,
             counters: dict) -> int:
    """Обработать окно, при переполнении разделив его (§14.4.1).

    Возвращает следующий свободный порядковый номер: номера сквозные по
    прогону, включая окна, появившиеся из деления, — иначе `UNIQUE
    (semantic_run_id, ordinal)` упал бы на первом же делении.
    """
    row = _window_row(models, window=window, run_id=run_id, source_id=source_id,
                      tenant_id=tenant_id, ordinal=ordinal, parent_id=parent_id)
    graph.add(row)
    graph.flush()
    next_ordinal = ordinal + 1

    try:
        extraction = extract(window.text, domain=domain, heading_path=window.heading_path,
                             model=model)
    except WindowTruncated:
        children = split_window(window)
        if len(children) < 2 or depth >= MAX_SPLIT_DEPTH:
            # Делить дальше нечего или некуда. Честный отказ, а не
            # обрезанный результат: §14.4.1 запрещает молча отбрасывать
            # остаток атомов.
            row.status = SemanticWindowStatus.FAILED
            row.error_code = "TRUNCATED_UNSPLITTABLE"
            counters["failed"] += 1
            graph.flush()
            return next_ordinal
        row.status = SemanticWindowStatus.SPLIT
        graph.flush()
        for child in children:
            next_ordinal = _process(
                graph, models, window=child, ordinal=next_ordinal, parent_id=row.id,
                depth=depth + 1, domain=domain, run_id=run_id, source_id=source_id,
                tenant_id=tenant_id, extract=extract, model=model, counters=counters)
        return next_ordinal
    except ExtractionFailed as exc:
        row.status = SemanticWindowStatus.FAILED
        row.error_code = "EXTRACTION_FAILED"
        counters["failed"] += 1
        logger.warning("окно %d источника %s не разобрано: %s", ordinal, source_id, exc)
        graph.flush()
        return next_ordinal

    row.rejected_count = len(extraction.rejected)
    row.result_hash = _result_hash(extraction)
    if extraction.is_empty:
        # «Модель ответила, извлекать нечего» — это NO_KNOWLEDGE, и у
        # него ЕСТЬ result_hash. Отличие от FAILED («мы не смогли») и от
        # PROCESSED с нулём узлов не косметическое: §14.4.1 требует,
        # чтобы аудит их различал.
        row.status = SemanticWindowStatus.NO_KNOWLEDGE
        counters["processed"] += 1
        counters["covered_chars"] += window.char_end - window.char_start
        graph.flush()
        return next_ordinal

    nodes, edges = _write_extraction(
        graph, models, extraction=extraction, window=window, window_row=row,
        run_id=run_id, source_id=source_id, tenant_id=tenant_id)
    row.status = SemanticWindowStatus.PROCESSED
    row.nodes_created = nodes
    row.edges_created = edges
    counters["processed"] += 1
    counters["nodes"] += nodes
    counters["edges"] += edges
    counters["covered_chars"] += window.char_end - window.char_start
    graph.flush()
    return next_ordinal


def publish_semantic_run(session: Session, *, source: KnowledgeSource, text: str,
                         model: str = DEFAULT_MODEL, extract=extract_window,
                         semantic_version: int = 2) -> PublishResult:
    """Разобрать источник целиком и опубликовать ревизию, если она годна.

    Единственная точка, которой разрешено менять `current_semantic_run_
    id`. Всё остальное — чтение.

    Возвращает результат, а не бросает исключение на плохом разборе:
    провал разбора не должен ронять ingest. L1 остаётся доступен поиску
    даже когда семантика деградировала (§14.19, §14.25: «A source may
    remain L1_READY + SEMANTIC_DEGRADED»).
    """
    tenant_id = source.knowledge_user_id
    if tenant_id is None:
        raise ValueError("источник без владельца не может иметь семантической ревизии")

    run = KnowledgeSemanticRun(
        knowledge_user_id=tenant_id, source_id=source.id, semantic_version=semantic_version,
        extractor_model=model, status=SemanticRunStatus.RUNNING,
        windows_total=0, windows_processed=0, windows_failed=0,
        nodes_created=0, edges_created=0, unresolved_candidates=0,
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()

    windows = build_windows(text)
    counters = {"processed": 0, "failed": 0, "nodes": 0, "edges": 0, "covered_chars": 0}
    use_health = is_health_domain(source.domain) and health_schema_configured()
    models = HEALTH_MODELS if use_health else PUBLIC_MODELS

    def run_windows(graph) -> None:
        ordinal = 0
        for window in windows:
            ordinal = _process(
                graph, models, window=window, ordinal=ordinal, parent_id=None, depth=0,
                domain=source.domain, run_id=run.id, source_id=source.id,
                tenant_id=tenant_id, extract=extract, model=model, counters=counters)
        counters["total"] = ordinal

    if use_health:
        with health_session(tenant_id) as graph:
            run_windows(graph)
    else:
        run_windows(session)

    total_chars = sum(w.char_end - w.char_start for w in windows)
    coverage = (counters["covered_chars"] / total_chars) if total_chars else 1.0

    run.windows_total = counters.get("total", 0)
    run.windows_processed = counters["processed"]
    run.windows_failed = counters["failed"]
    run.nodes_created = counters["nodes"]
    run.edges_created = counters["edges"]
    run.coverage_ratio = round(coverage, 3)
    run.finished_at = datetime.now(timezone.utc)

    # Единственное условие READY: ни одно окно не провалено. Покрытие
    # при этом равно единице по построению — «терминально и не FAILED»
    # означает, что участок разобран, — но считается отдельно, чтобы
    # §14.19 мог показать владельцу честные 94%, а не только да/нет.
    if counters["failed"] == 0:
        run.status = SemanticRunStatus.READY
    elif counters["processed"] > 0:
        run.status = SemanticRunStatus.DEGRADED
        run.error_code = "WINDOWS_FAILED"
    else:
        run.status = SemanticRunStatus.FAILED
        run.error_code = "ALL_WINDOWS_FAILED"
    session.flush()

    switched = False
    if run.status == SemanticRunStatus.READY:
        # Атомарное переключение: один UPDATE. База проверяет его сама
        # (триггер R2-hardening) — если ревизия вдруг не READY, не того
        # источника или не того владельца, запрос упадёт, а не запишет
        # мусор.
        session.execute(
            sql_text("UPDATE knowledge_sources SET current_semantic_run_id = :run "
                     "WHERE id = :source"),
            {"run": run.id, "source": source.id})
        session.flush()
        switched = True

    return PublishResult(
        run_id=run.id, status=run.status, switched=switched,
        windows_total=run.windows_total, windows_processed=run.windows_processed,
        windows_failed=run.windows_failed, nodes_created=run.nodes_created,
        edges_created=run.edges_created, coverage_ratio=float(run.coverage_ratio),
    )
