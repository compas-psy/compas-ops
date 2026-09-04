"""R4 (§14.18) — бенчмарк-харнесс: прогон golden/shadow наборов, hard gates,
выбор production-модели извлекателя.

Что этот модуль НЕ делает:
  - не вызывает `publish_semantic_run()` и не пишет узлы/рёбра в канонический
    граф (владелец п.1: «Не использовать publish_semantic_run() для owner
    corpus и не писать candidate results в canonical graph»);
  - не хранит сырой текст источника и сырой ответ модели в отчёте —
    `ShadowWindowSample`/`ShadowCaseResult` носят только счётчики и хэш окна
    (владелец п.2Б: «report contains only aggregate metrics/source_id/window
    hash»);
  - не решает, ЧТО замерять на реальном корпусе — эта функция принимает уже
    готовые окна (`ShadowWindowSample`), а откуда они взяты на живом сервере
    (health или нет, из какого source) решает вызывающий live-скрипт, не он.

Golden-прогон (`run_golden_benchmark`) и shadow-прогон (`run_shadow_benchmark`)
оба работают через РЕАЛЬНЫЙ `extract_nodes_window()` (P1, владелец
2026-09-04: node-only production path — модель возвращает entities+atoms,
edges строит исключительно `relation_compiler.py`) — не копию логики, иначе
бенчмарк мерил бы не то, что окажется в production.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import inspect
import json
import logging
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from . import semantic_extract as semantic_extract_module
from .relation_compiler import compile_relations
from .semantic_benchmark_fixtures import GOLDEN_CASES, GoldenCase
from .semantic_benchmark_metrics import AggregateMetrics, CaseScore, aggregate, evaluate_case
from .semantic_extract import (
    MAX_ATOMS_PER_WINDOW, ExtractedAtom, ExtractedEdge, ExtractedEntity, ExtractionFailed,
    WindowExtraction, WindowTruncated, extract_nodes_window,
)

#: Представительное подмножество golden-кейсов для замера стабильности
#: (R4 п.4: «Повтори representative subset несколько раз, измерь
#: stability»). Не все 21 — иначе стабильность мерилась бы столько же
#: времени, сколько сам прогон, а представительность важнее числа.
STABILITY_SUBSET_IDS = (
    "doctor_visit", "date_unknown", "same_label_different_entities",
    "negative_statement", "long_dense_window",
)


@dataclass
class CaseRun:
    case_id: str
    categories: tuple[str, ...]
    outcome: str  # processed | no_knowledge | failed | truncated
    latency_seconds: float
    repair_attempts: int
    score: CaseScore | None = None
    error: str | None = None
    #: R4.7 (владелец 04.09.2026, final acceptance requirement 5) —
    #: провенанс рёбер: `proposed` — то, что вернула модель в
    #: `extraction.edges` ДО перезаписи; `compiled` — вывод
    #: `compile_relations()`, который и стал `extraction.edges` для
    #: `evaluate_case()` ниже. Оба поля существуют для того, чтобы
    #: R4 final acceptance могла показать разницу (модель предлагает,
    #: компилятор решает), не для скоринга — `score` уже посчитан
    #: только по `compiled`.
    proposed_edges_count: int = 0
    compiled_edges_count: int = 0


@dataclass
class SchemaStats:
    cases_total: int = 0
    first_pass_success: int = 0
    repaired_success: int = 0
    failed_cases: int = 0
    truncated_cases: int = 0
    total_repair_attempts: int = 0
    malformed_results: int = 0

    @property
    def first_pass_rate(self) -> float:
        return self.first_pass_success / self.cases_total if self.cases_total else 0.0

    @property
    def avg_repair_attempts(self) -> float:
        return self.total_repair_attempts / self.cases_total if self.cases_total else 0.0

    @property
    def processed_window_coverage(self) -> float:
        """§14.18 hard gate «processed-window coverage 100%». Окно НЕ
        обработано терминально, если его исход — failed/truncated/
        malformed (ни один из них не даёт `CaseScore`); "no_knowledge" и
        "processed" — оба терминальные успешные исходы, обработка
        состоялась, что бы модель ни вернула."""
        if not self.cases_total:
            return 0.0
        unprocessed = self.failed_cases + self.truncated_cases + self.malformed_results
        return (self.cases_total - unprocessed) / self.cases_total


@dataclass
class StabilityResult:
    case_id: str
    runs: int
    identical_runs: int

    @property
    def reproducible(self) -> bool:
        return self.runs > 0 and self.identical_runs == self.runs


def _extraction_signature(extraction: WindowExtraction) -> tuple:
    """Каноническая форма ответа для сравнения «тот же результат / другой».
    Не хэш JSON-строки — порядок полей/списков модель не обязана
    сохранять между запросами, а нас интересует СОДЕРЖАНИЕ, не байты."""
    return (
        tuple(sorted((e.local_id, e.entity_type, e.label, e.subtype, tuple(sorted(e.aliases)))
                     for e in extraction.entities)),
        tuple(sorted((a.local_id, a.kind, a.title, a.text, a.occurred_at, a.date_precision)
                     for a in extraction.atoms)),
        tuple(sorted((e.from_local_id, e.relation_type, e.to_local_id, e.role)
                     for e in extraction.edges)),
    )


def _validate_structure(extraction: WindowExtraction) -> bool:
    """Защитный повторный контроль инвариантов схемы (R4 п.7, gate
    «validator/repair can publish malformed result»). На реальном пути
    сработать не должен никогда — ни `extract_window()` (через
    `validate()`), ни `extract_nodes_window()` (через `_validate_nodes()`,
    R4 EXIT FIX 2026-09-04) физически не могут вернуть иное; это
    дублирующая проверка САМОГО харнесса, а не признак того, что
    production-путь её не делает."""
    for e in extraction.entities:
        if not e.local_id or not e.label or not e.entity_type:
            return False
    for a in extraction.atoms:
        if not a.local_id or not a.title or not a.text or not a.kind:
            return False
    if len(extraction.atoms) >= MAX_ATOMS_PER_WINDOW:
        return False
    return True


#: P7 (владелец 2026-09-04) — synthetic-only raw diagnostics: полные
#: entities/atoms/compiled_edges/rejected/split_lineage конкретного golden
#: run'а, чтобы упавший final acceptance можно было разобрать по точным
#: mismatch, а не гадать по агрегатным счётчикам (см. R4 RCA data gap).
#: НИКОГДА не заполняется для `run_shadow_benchmark()` (реальный
#: пользовательский корпус) — тот вызов `_run_case`/`extract_fn` этот
#: параметр не передаёт вовсе, не полагается на «не забыть выключить».
def _entity_diagnostic(e: ExtractedEntity) -> dict:
    return {"local_id": e.local_id, "entity_type": e.entity_type, "label": e.label,
            "evidence_quote": e.evidence_quote}


def _atom_diagnostic(a: ExtractedAtom) -> dict:
    return {"local_id": a.local_id, "kind": a.kind, "text": a.text,
            "evidence_quote": a.evidence_quote}


def _edge_diagnostic(e: ExtractedEdge) -> dict:
    return {"from_local_id": e.from_local_id, "relation_type": e.relation_type,
            "to_local_id": e.to_local_id, "evidence_quote": e.evidence_quote}


def _append_raw_diagnostic(raw_diagnostics: list[dict] | None, case_id: str,
                           extraction: WindowExtraction | None = None,
                           compiled_edges: Sequence[ExtractedEdge] = (),
                           lineage: Sequence[dict] = ()) -> None:
    if raw_diagnostics is None:
        return
    raw_diagnostics.append({
        "case_id": case_id,
        "entities": [_entity_diagnostic(e) for e in (extraction.entities if extraction else [])],
        "atoms": [_atom_diagnostic(a) for a in (extraction.atoms if extraction else [])],
        "compiled_edges": [_edge_diagnostic(e) for e in compiled_edges],
        "rejected": list(extraction.rejected) if extraction else [],
        "split_lineage": list(lineage),
    })


def _run_case(case: GoldenCase, *, model: str, keep_alive: str | None, extract_fn,
             raw_diagnostics: list[dict] | None = None) -> CaseRun:
    handler_records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            handler_records.append(record.getMessage())

    logger = logging.getLogger("helm_core.knowledge.semantic_extract")
    capture = _Capture()
    logger.addHandler(capture)
    lineage: list[dict] = []
    extract_kwargs = {"_lineage": lineage} if (
        raw_diagnostics is not None and extract_fn is extract_nodes_window) else {}
    t0 = time.monotonic()
    try:
        extraction = extract_fn(case.text, domain=case.domain, heading_path=case.heading_path,
                                model=model, keep_alive=keep_alive, **extract_kwargs)
    except WindowTruncated as exc:
        _append_raw_diagnostic(raw_diagnostics, case.case_id, lineage=lineage)
        return CaseRun(case_id=case.case_id, categories=case.categories, outcome="truncated",
                       latency_seconds=time.monotonic() - t0, repair_attempts=len(handler_records),
                       error=str(exc))
    except ExtractionFailed as exc:
        _append_raw_diagnostic(raw_diagnostics, case.case_id, lineage=lineage)
        return CaseRun(case_id=case.case_id, categories=case.categories, outcome="failed",
                       latency_seconds=time.monotonic() - t0, repair_attempts=len(handler_records),
                       error=str(exc))
    finally:
        logger.removeHandler(capture)
    latency = time.monotonic() - t0

    # R4.7 (владелец 03.09.2026): production-путь больше не доверяет
    # `extraction.edges`, предложенные моделью — единственный источник
    # рёбер начиная с R4.7 это deterministic compiler, на уже
    # провалидированных entities/atoms этого же прогона. Заменяем ДО
    # `_validate_structure`/`evaluate_case`, чтобы бенчмарк измерял
    # ровно то, что попадёт в production, а не устаревший LLM-путь.
    proposed_edges_count = len(extraction.edges)
    compiled_edges = compile_relations(extraction.entities, extraction.atoms, case.text)
    # R4.7 final acceptance (владелец 04.09.2026, requirement 5) —
    # `compile_relations()` документирован как чистая детерминированная
    # функция без единого случайного/LLM источника; здесь это не
    # оптимистичное допущение, а проверяемый инвариант — повторный вызов
    # на ТЕХ ЖЕ входах обязан дать тот же результат. Расхождение здесь
    # означало бы реальный баг компилятора (скрытая недетерминированность),
    # и по требованию owner такая ошибка обязана провалить прогон целиком
    # (`RuntimeError` → `evaluator exception → FAIL`), а не тихо
    # разойтись с тем, что попадёт в `R4_FINAL_ACCEPTANCE.json`.
    verification_edges = compile_relations(extraction.entities, extraction.atoms, case.text)
    key = lambda edges: sorted((e.from_local_id, e.relation_type, e.to_local_id, e.role) for e in edges)
    if key(compiled_edges) != key(verification_edges):
        raise RuntimeError(
            f"{case.case_id}: compile_relations() недетерминирован — "
            f"два вызова на тех же entities/atoms дали разные рёбра")
    extraction.edges = compiled_edges

    if not _validate_structure(extraction):
        _append_raw_diagnostic(raw_diagnostics, case.case_id, extraction=extraction,
                               compiled_edges=compiled_edges, lineage=lineage)
        return CaseRun(case_id=case.case_id, categories=case.categories, outcome="malformed",
                       latency_seconds=latency, repair_attempts=len(handler_records),
                       error="структурный инвариант нарушен после validate()",
                       proposed_edges_count=proposed_edges_count,
                       compiled_edges_count=len(compiled_edges))

    score = evaluate_case(case, extraction)
    outcome = "no_knowledge" if extraction.is_empty else "processed"
    _append_raw_diagnostic(raw_diagnostics, case.case_id, extraction=extraction,
                           compiled_edges=compiled_edges, lineage=lineage)
    return CaseRun(case_id=case.case_id, categories=case.categories, outcome=outcome,
                   latency_seconds=latency, repair_attempts=len(handler_records), score=score,
                   proposed_edges_count=proposed_edges_count, compiled_edges_count=len(compiled_edges))


@dataclass
class GoldenBenchmarkReport:
    model: str
    keep_alive: str | None
    runs: list[CaseRun]
    schema_stats: SchemaStats
    stability: list[StabilityResult]
    metrics: AggregateMetrics
    latencies_seconds: list[float]

    @property
    def p50_latency(self) -> float:
        return statistics.median(self.latencies_seconds) if self.latencies_seconds else 0.0

    @property
    def p95_latency(self) -> float:
        if not self.latencies_seconds:
            return 0.0
        ordered = sorted(self.latencies_seconds)
        idx = max(0, int(round(0.95 * (len(ordered) - 1))))
        return ordered[idx]


def run_golden_benchmark(*, model: str, keep_alive: str | None = None,
                         extract_fn=extract_nodes_window, stability_repeats: int = 3,
                         cases: tuple[GoldenCase, ...] = GOLDEN_CASES,
                         raw_diagnostics: list[dict] | None = None) -> GoldenBenchmarkReport:
    """`raw_diagnostics` (P7, владелец 2026-09-04): передай пустой список,
    чтобы получить per-case entities/atoms/compiled_edges/rejected/
    split_lineage synthetic golden-корпуса (см. `_append_raw_diagnostic`)
    — НЕ хранится в `GoldenBenchmarkReport`/`R4_FINAL_ACCEPTANCE.json`,
    только в этом отдельном списке, вызывающий код сам решает, куда его
    писать. `run_shadow_benchmark()` этот параметр не принимает вовсе —
    для реального пользовательского корпуса сырые diagnostics не
    собираются НИКОГДА, не «выключены по умолчанию»."""
    runs: list[CaseRun] = []
    schema = SchemaStats(cases_total=len(cases))
    for case in cases:
        run = _run_case(case, model=model, keep_alive=keep_alive, extract_fn=extract_fn,
                        raw_diagnostics=raw_diagnostics)
        runs.append(run)
        if run.outcome == "failed":
            schema.failed_cases += 1
        elif run.outcome == "truncated":
            schema.truncated_cases += 1
        elif run.outcome == "malformed":
            schema.malformed_results += 1
        else:
            schema.total_repair_attempts += run.repair_attempts
            if run.repair_attempts == 0:
                schema.first_pass_success += 1
            else:
                schema.repaired_success += 1

    stability: list[StabilityResult] = []
    by_id = {c.case_id: c for c in cases}
    for case_id in STABILITY_SUBSET_IDS:
        case = by_id.get(case_id)
        if case is None:
            continue
        first_run = next((r for r in runs if r.case_id == case_id), None)
        if first_run is None or first_run.outcome in ("failed", "truncated", "malformed"):
            stability.append(StabilityResult(case_id=case_id, runs=0, identical_runs=0))
            continue
        signatures = []
        for _ in range(stability_repeats):
            extraction = extract_fn(case.text, domain=case.domain, heading_path=case.heading_path,
                                    model=model, keep_alive=keep_alive)
            signatures.append(_extraction_signature(extraction))
        identical = sum(1 for sig in signatures if sig == signatures[0])
        stability.append(StabilityResult(case_id=case_id, runs=len(signatures), identical_runs=identical))

    scores = [r.score for r in runs if r.score is not None]
    metrics = aggregate(scores)
    latencies = [r.latency_seconds for r in runs]
    return GoldenBenchmarkReport(model=model, keep_alive=keep_alive, runs=runs, schema_stats=schema,
                                 stability=stability, metrics=metrics, latencies_seconds=latencies)


@dataclass(frozen=True)
class ShadowWindowSample:
    """Одно окно реального корпуса. `text` уходит в `extract_fn` и НИКУДА
    больше — отчёт хранит только `window_hash` (совпадает по построению с
    `semantic_windows.SemanticWindow.text_hash`: тот же sha256 от текста)."""

    source_id: str
    domain: str
    window_ordinal: int
    text: str
    heading_path: tuple[str, ...] = ()

    @property
    def window_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass
class ShadowCaseResult:
    source_id: str
    domain: str
    window_ordinal: int
    window_hash: str
    outcome: str
    latency_seconds: float
    repair_attempts: int
    entities_count: int
    atoms_count: int
    edges_count: int
    rejected_count: int


@dataclass
class ShadowBenchmarkReport:
    model: str
    keep_alive: str | None
    results: list[ShadowCaseResult]

    @property
    def windows_total(self) -> int:
        return len(self.results)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == "failed")

    @property
    def truncated_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == "truncated")

    @property
    def no_knowledge_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == "no_knowledge")

    @property
    def by_domain(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.domain] = counts.get(r.domain, 0) + 1
        return counts


def run_shadow_benchmark(samples: list[ShadowWindowSample], *, model: str,
                         keep_alive: str | None = None,
                         extract_fn=extract_nodes_window) -> ShadowBenchmarkReport:
    """Операционный прогон на реальных окнах: считает ТОЛЬКО поведение
    (успех схемы/латентность/счётчики), не «правильность» — для реального
    корпуса нет заранее известного gold, и выдавать самопорождённый ответ
    модели за истину владелец запретил явно (R4 п.2Б)."""
    results: list[ShadowCaseResult] = []
    for sample in samples:
        t0 = time.monotonic()
        try:
            extraction = extract_fn(sample.text, domain=sample.domain,
                                    heading_path=sample.heading_path, model=model,
                                    keep_alive=keep_alive)
        except WindowTruncated:
            results.append(ShadowCaseResult(
                source_id=sample.source_id, domain=sample.domain,
                window_ordinal=sample.window_ordinal, window_hash=sample.window_hash,
                outcome="truncated", latency_seconds=time.monotonic() - t0, repair_attempts=0,
                entities_count=0, atoms_count=0, edges_count=0, rejected_count=0))
            continue
        except ExtractionFailed:
            results.append(ShadowCaseResult(
                source_id=sample.source_id, domain=sample.domain,
                window_ordinal=sample.window_ordinal, window_hash=sample.window_hash,
                outcome="failed", latency_seconds=time.monotonic() - t0, repair_attempts=0,
                entities_count=0, atoms_count=0, edges_count=0, rejected_count=0))
            continue
        latency = time.monotonic() - t0
        extraction.edges = compile_relations(extraction.entities, extraction.atoms, sample.text)
        outcome = "no_knowledge" if extraction.is_empty else "processed"
        results.append(ShadowCaseResult(
            source_id=sample.source_id, domain=sample.domain, window_ordinal=sample.window_ordinal,
            window_hash=sample.window_hash, outcome=outcome, latency_seconds=latency,
            repair_attempts=0, entities_count=len(extraction.entities),
            atoms_count=len(extraction.atoms), edges_count=len(extraction.edges),
            rejected_count=len(extraction.rejected)))
    return ShadowBenchmarkReport(model=model, keep_alive=keep_alive, results=results)


def golden_report_to_dict(report: GoldenBenchmarkReport) -> dict:
    """Сериализация для CLI/отчёта. `dataclasses.asdict` не подхватывает
    @property (p50/p95) — их считаем явно."""
    data = dataclasses.asdict(report)
    data["p50_latency"] = report.p50_latency
    data["p95_latency"] = report.p95_latency
    return data


def golden_report_from_dict(data: dict) -> GoldenBenchmarkReport:
    """Обратное к `golden_report_to_dict` — разбор JSON, напечатанного
    живым CLI на сервере, вне сервера (выбор winner по уже полученным
    данным не должен требовать ещё одного обращения к Ollama, п.10:
    сравнение кандидатов не зависит от того, что сервер ответит в
    следующий раз чуть иначе)."""
    def _score_from_dict(s: dict | None) -> CaseScore | None:
        if s is None:
            return None
        return CaseScore(**{**s, "categories": tuple(s["categories"])})

    runs = [
        CaseRun(case_id=r["case_id"], categories=tuple(r["categories"]), outcome=r["outcome"],
                latency_seconds=r["latency_seconds"], repair_attempts=r["repair_attempts"],
                score=_score_from_dict(r["score"]), error=r["error"],
                proposed_edges_count=r.get("proposed_edges_count", 0),
                compiled_edges_count=r.get("compiled_edges_count", 0))
        for r in data["runs"]
    ]
    return GoldenBenchmarkReport(
        model=data["model"], keep_alive=data["keep_alive"], runs=runs,
        schema_stats=SchemaStats(**{k: v for k, v in data["schema_stats"].items()
                                    if k in SchemaStats.__dataclass_fields__}),
        stability=[StabilityResult(**s) for s in data["stability"]],
        metrics=AggregateMetrics(**{k: v for k, v in data["metrics"].items()
                                    if k in AggregateMetrics.__dataclass_fields__}),
        latencies_seconds=data["latencies_seconds"],
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_of_module_source(module) -> str:
    with open(inspect.getsourcefile(module), "rb") as f:
        return _sha256_bytes(f.read())


def compute_fingerprint(*, git_sha: str, model_tag: str, model_digest: str,
                        keep_alive: str | None, run_id: str) -> dict:
    """R4 retraction п.4: старый результат переиспользуется ТОЛЬКО при
    полном совпадении. Каждый вход, который может изменить, что именно
    измеряется, — своим полем: код извлечения, промпт, схема ответа,
    сами фикстуры и харнесс могут разойтись с закоммиченным состоянием
    независимо друг от друга, и любое расхождение должно быть видно, а
    не молчаливо проигнорировано."""
    import helm_core.knowledge.semantic_benchmark_fixtures as fixtures_module

    fingerprint = {
        "git_sha": git_sha,
        "semantic_extract_sha256": _sha256_of_module_source(semantic_extract_module),
        "system_prompt_sha256": _sha256_bytes(semantic_extract_module.SYSTEM_PROMPT.encode()),
        "response_schema_sha256": _sha256_bytes(
            json.dumps(semantic_extract_module.RESPONSE_SCHEMA, sort_keys=True).encode()),
        "golden_fixtures_sha256": _sha256_of_module_source(fixtures_module),
        "benchmark_harness_sha256": _sha256_of_module_source(
            __import__("sys").modules[__name__]),
        "seed": semantic_extract_module.DETERMINISTIC_SEED,
        "model_tag": model_tag,
        "model_digest": model_digest,
        "keep_alive": keep_alive,
        "run_id": run_id,
    }
    fingerprint["fingerprint_hash"] = _sha256_bytes(
        json.dumps(fingerprint, sort_keys=True).encode())
    return fingerprint


def _cli_fingerprint(args: argparse.Namespace) -> None:
    fp = compute_fingerprint(git_sha=args.git_sha, model_tag=args.model,
                             model_digest=args.model_digest, keep_alive=args.keep_alive,
                             run_id=args.run_id)
    print(json.dumps(fp, ensure_ascii=False, indent=2))


def _cli_validate(args: argparse.Namespace) -> None:
    """R4 retraction п.3: частичный/невалидный JSON никогда не считается
    завершённым результатом. Вызывается ПЕРЕД atomic `mv result.json.tmp
    result.json` — падение здесь означает, что временный файл отбрасывается,
    а не публикуется под финальным именем."""
    errors: list[str] = []
    try:
        with open(args.file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INVALID: не удалось прочитать/разобрать JSON: {exc}")
        raise SystemExit(1) from exc

    if data.get("model") != args.expect_model:
        errors.append(f"model: ожидали {args.expect_model!r}, получили {data.get('model')!r}")

    case_ids = [r.get("case_id") for r in data.get("runs", [])]
    if len(case_ids) != len(set(case_ids)):
        errors.append("runs содержит повторяющийся case_id")
    expected_ids = {c.case_id for c in GOLDEN_CASES}
    if set(case_ids) != expected_ids:
        errors.append(
            f"набор case_id не совпадает с golden fixtures: "
            f"нет {sorted(expected_ids - set(case_ids))}, лишние {sorted(set(case_ids) - expected_ids)}")
    if data.get("schema_stats", {}).get("cases_total") != len(GOLDEN_CASES):
        errors.append(
            f"schema_stats.cases_total={data.get('schema_stats', {}).get('cases_total')!r}, "
            f"ожидали {len(GOLDEN_CASES)}")

    if args.expect_fingerprint_hash:
        actual_hash = (data.get("fingerprint") or {}).get("fingerprint_hash")
        if actual_hash != args.expect_fingerprint_hash:
            errors.append(
                f"fingerprint_hash не совпадает: ожидали {args.expect_fingerprint_hash!r}, "
                f"в файле {actual_hash!r}")

    if errors:
        for e in errors:
            print(f"INVALID: {e}")
        raise SystemExit(1)
    print("VALID")


def _cli_golden(args: argparse.Namespace) -> None:
    cases = GOLDEN_CASES
    if args.case:
        cases = tuple(c for c in GOLDEN_CASES if c.case_id in set(args.case))
        missing = set(args.case) - {c.case_id for c in cases}
        if missing:
            raise SystemExit(f"неизвестные case_id: {sorted(missing)}")
    # P7 (владелец 2026-09-04): raw diagnostics — отдельный файл, НЕ поле
    # печатаемого report'а — R4 п.2Б требует, чтобы result.json (и всё,
    # что попадёт в R4_FINAL_ACCEPTANCE.json) оставался только агрегатом;
    # сырые entities/atoms/compiled_edges существуют исключительно для
    # синтетического golden-корпуса и только если явно запрошены флагом.
    raw_diagnostics: list[dict] | None = [] if args.raw_diagnostics_out else None
    report = run_golden_benchmark(model=args.model, keep_alive=args.keep_alive,
                                  stability_repeats=args.stability_repeats, cases=cases,
                                  raw_diagnostics=raw_diagnostics)
    data = golden_report_to_dict(report)
    if args.git_sha and args.model_digest:
        data["fingerprint"] = compute_fingerprint(
            git_sha=args.git_sha, model_tag=args.model, model_digest=args.model_digest,
            keep_alive=args.keep_alive, run_id=args.run_id or "")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if args.raw_diagnostics_out:
        with open(args.raw_diagnostics_out, "w", encoding="utf-8") as f:
            json.dump(raw_diagnostics, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    golden = sub.add_parser("golden", help="Golden fixture benchmark одного кандидата")
    golden.add_argument("--model", required=True)
    golden.add_argument("--keep-alive", default=None)
    golden.add_argument("--stability-repeats", type=int, default=3)
    golden.add_argument("--case", action="append", default=None,
                        help="Ограничить прогон конкретными case_id (можно несколько раз)")
    golden.add_argument("--git-sha", default=None,
                        help="Вместе с --model-digest включает fingerprint в вывод (R4 п.4)")
    golden.add_argument("--model-digest", default=None)
    golden.add_argument("--run-id", default=None)
    golden.add_argument("--raw-diagnostics-out", default=None,
                        help="P7 (владелец 2026-09-04): путь для synthetic-only raw "
                             "diagnostics (entities/atoms/compiled_edges/rejected/split_lineage "
                             "по каждому golden case) — НИКОГДА не для реального корпуса")

    fingerprint = sub.add_parser("fingerprint",
                                 help="Только fingerprint, без обращения к Ollama (для решения reuse/new-run)")
    fingerprint.add_argument("--model", required=True)
    fingerprint.add_argument("--keep-alive", default=None)
    fingerprint.add_argument("--git-sha", required=True)
    fingerprint.add_argument("--model-digest", required=True)
    fingerprint.add_argument("--run-id", default="")

    validate = sub.add_parser("validate", help="Проверить result.json.tmp перед atomic mv (R4 п.3)")
    validate.add_argument("--file", required=True)
    validate.add_argument("--expect-model", required=True)
    validate.add_argument("--expect-fingerprint-hash", default=None)

    args = parser.parse_args()
    if args.mode == "golden":
        _cli_golden(args)
    elif args.mode == "fingerprint":
        _cli_fingerprint(args)
    elif args.mode == "validate":
        _cli_validate(args)


if __name__ == "__main__":
    main()
