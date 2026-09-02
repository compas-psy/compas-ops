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
оба работают через РЕАЛЬНЫЙ `extract_window()` — не копию логики, иначе
бенчмарк мерил бы не то, что окажется в production.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import statistics
import time
from dataclasses import dataclass, field

from .semantic_benchmark_fixtures import GOLDEN_CASES, GoldenCase
from .semantic_benchmark_metrics import AggregateMetrics, CaseScore, aggregate, evaluate_case
from .semantic_extract import (
    MAX_ATOMS_PER_WINDOW, ExtractionFailed, WindowExtraction, WindowTruncated, extract_window,
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
    «validator/repair can publish malformed result»). При реальном пути
    через `validate()` сработать не должен никогда — `extract_window()`
    физически не может вернуть иное; это дублирующая проверка САМОГО
    харнесса, а не признак того, что production-путь её не делает."""
    for e in extraction.entities:
        if not e.local_id or not e.label or not e.entity_type:
            return False
    for a in extraction.atoms:
        if not a.local_id or not a.title or not a.text or not a.kind:
            return False
    if len(extraction.atoms) >= MAX_ATOMS_PER_WINDOW:
        return False
    return True


def _run_case(case: GoldenCase, *, model: str, keep_alive: str | None, extract_fn) -> CaseRun:
    handler_records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            handler_records.append(record.getMessage())

    logger = logging.getLogger("helm_core.knowledge.semantic_extract")
    capture = _Capture()
    logger.addHandler(capture)
    t0 = time.monotonic()
    try:
        extraction = extract_fn(case.text, domain=case.domain, heading_path=case.heading_path,
                                model=model, keep_alive=keep_alive)
    except WindowTruncated as exc:
        return CaseRun(case_id=case.case_id, categories=case.categories, outcome="truncated",
                       latency_seconds=time.monotonic() - t0, repair_attempts=len(handler_records),
                       error=str(exc))
    except ExtractionFailed as exc:
        return CaseRun(case_id=case.case_id, categories=case.categories, outcome="failed",
                       latency_seconds=time.monotonic() - t0, repair_attempts=len(handler_records),
                       error=str(exc))
    finally:
        logger.removeHandler(capture)
    latency = time.monotonic() - t0

    if not _validate_structure(extraction):
        return CaseRun(case_id=case.case_id, categories=case.categories, outcome="malformed",
                       latency_seconds=latency, repair_attempts=len(handler_records),
                       error="структурный инвариант нарушен после validate()")

    score = evaluate_case(case, extraction)
    outcome = "no_knowledge" if extraction.is_empty else "processed"
    return CaseRun(case_id=case.case_id, categories=case.categories, outcome=outcome,
                   latency_seconds=latency, repair_attempts=len(handler_records), score=score)


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
                         extract_fn=extract_window, stability_repeats: int = 3,
                         cases: tuple[GoldenCase, ...] = GOLDEN_CASES) -> GoldenBenchmarkReport:
    runs: list[CaseRun] = []
    schema = SchemaStats(cases_total=len(cases))
    for case in cases:
        run = _run_case(case, model=model, keep_alive=keep_alive, extract_fn=extract_fn)
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
                         extract_fn=extract_window) -> ShadowBenchmarkReport:
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
                score=_score_from_dict(r["score"]), error=r["error"])
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


def _cli_golden(args: argparse.Namespace) -> None:
    cases = GOLDEN_CASES
    if args.case:
        cases = tuple(c for c in GOLDEN_CASES if c.case_id in set(args.case))
        missing = set(args.case) - {c.case_id for c in cases}
        if missing:
            raise SystemExit(f"неизвестные case_id: {sorted(missing)}")
    report = run_golden_benchmark(model=args.model, keep_alive=args.keep_alive,
                                  stability_repeats=args.stability_repeats, cases=cases)
    print(json.dumps(golden_report_to_dict(report), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    golden = sub.add_parser("golden", help="Golden fixture benchmark одного кандидата")
    golden.add_argument("--model", required=True)
    golden.add_argument("--keep-alive", default=None)
    golden.add_argument("--stability-repeats", type=int, default=3)
    golden.add_argument("--case", action="append", default=None,
                        help="Ограничить прогон конкретными case_id (можно несколько раз)")

    args = parser.parse_args()
    if args.mode == "golden":
        _cli_golden(args)


if __name__ == "__main__":
    main()
