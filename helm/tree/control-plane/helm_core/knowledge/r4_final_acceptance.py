"""R4.7 final acceptance (владелец 04.09.2026) — единственный финальный
live E2E acceptance run для уже выбранного `qwen2.5:7b`
(`docs/KNOWLEDGE_MODELS.md`, раздел «Выбор pass1 extractor»).

Это НЕ новый benchmark и НЕ новая реализация scoring. `qwen2.5:7b` здесь
не candidate — зафиксированная acceptance-конфигурация: этот модуль не
запускает `gemma2:2b`/`qwen2.5:3b`, не вызывает `select_winner()`/
`_ranking_key()` (сравнительный отбор), не строит ranking. Единственный
вопрос: проходит ли уже выбранная модель ВСЕ нормативные §14.18 hard
gates разом — этот ответ целиком даёт уже существующий, протестированный
`semantic_benchmark_selection.evaluate_hard_gates()` (R4.5.6), вызванный
здесь как есть, ни одна строчка его scoring-логики не скопирована заново.

Acceptance = строгое AND всех гейтов. Владелец, requirement 4: любой
missing/null metric, любое исключение evaluator/compiler, любой неполный
прогон — FAIL. Никакого "winner несмотря на провалённые гейты" — здесь
нет ranking, которому было бы что "несмотря" считать.

Requirement 5 (доказать, что оценивались deterministic-compiled edges,
не старые LLM-proposed): report `GoldenBenchmarkReport` намеренно НЕ
хранит сырые entities/atoms/edges (R4 п.2Б, `semantic_benchmark.py`
docstring — «report contains only aggregate metrics») — значит
независимая перепроверка данных ПОСЛЕ прогона, по содержимому отчёта,
невозможна. Доказательство здесь — структурное, на уровне исходного
кода `_run_case()` (`verify_compiler_is_sole_edge_source`), тем же
классом инварианта, что уже применяет `test_extraction_never_leaves_
the_machine` для zero-paid (`verify_zero_cloud_relation_extraction`,
requirement 7). Если структурное доказательство не проходит — acceptance
не запускается вообще (владелец: «Если это невозможно доказать
программно — live run НЕ запускать»)."""

from __future__ import annotations

import argparse
import ast
import dataclasses
import inspect
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import relation_compiler as relation_compiler_module
from . import semantic_benchmark as semantic_benchmark_module
from . import semantic_benchmark_fixtures as semantic_benchmark_fixtures_module
from . import semantic_extract as semantic_extract_module
from .semantic_benchmark import GoldenBenchmarkReport, golden_report_from_dict
from .semantic_benchmark_selection import CandidateResult, ResourceStats, evaluate_hard_gates

#: Версия набора требований §14.18, которую здесь проверяет
#: `evaluate_hard_gates()` — меняется ТОЛЬКО вместе с самим набором
#: гейтов, не с каждым прогоном (владелец, requirement 6: «gate
#: specification revision»).
GATE_SPEC_REVISION = "R4.7-2026-09-04"


def verify_compiler_is_sole_edge_source() -> None:
    """Requirement 5: структурное (не рантайм-данные) доказательство,
    что `extraction.edges`, попавшее в `evaluate_case()`, — РОВНО вывод
    `compile_relations()`, не то, что предложила модель. Разбирает
    исходник `_run_case()`: должно быть ровно одно присваивание
    `extraction.edges = <имя>`, и это `<имя>` обязано, в свою очередь,
    иметь ровно одно присваивание во всей функции — прямому вызову
    `compile_relations(...)` (не переприсваивается, не проксируется
    через что-то ещё). Провал здесь — RuntimeError, что по контракту
    `build_acceptance` (ниже) превращается в FAIL всего прогона."""
    source = inspect.getsource(semantic_benchmark_module._run_case)
    tree = ast.parse(source)
    func = tree.body[0]
    edge_assignments = [
        node for node in ast.walk(func)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "edges" for t in node.targets)
    ]
    if len(edge_assignments) != 1:
        raise RuntimeError(
            f"_run_case(): ожидалось ровно одно присваивание extraction.edges, "
            f"найдено {len(edge_assignments)} — недоказуемо программно")
    rhs = edge_assignments[0].value
    if isinstance(rhs, ast.Call) and isinstance(rhs.func, ast.Name) and rhs.func.id == "compile_relations":
        return  # прямой вызов на месте присваивания — простейший, тоже валидный случай
    if not isinstance(rhs, ast.Name):
        raise RuntimeError(
            f"_run_case(): extraction.edges присваивается не из имени и не напрямую из "
            f"compile_relations(...) — недоказуемо программно (узел {ast.dump(rhs)[:120]})")
    var_name = rhs.id
    var_assignments = [
        node for node in ast.walk(func)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == var_name for t in node.targets)
    ]
    if len(var_assignments) != 1:
        raise RuntimeError(
            f"_run_case(): переменная {var_name!r} присваивается {len(var_assignments)} раз(а) — "
            f"недоказуемо программно, что она всегда равна выводу compile_relations()")
    call = var_assignments[0].value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "compile_relations"):
        called = getattr(getattr(call, "func", None), "id", repr(call))
        raise RuntimeError(
            f"_run_case(): {var_name!r} присваивается не из compile_relations(...), "
            f"а из {called!r} — недоказуемо программно")


def verify_zero_cloud_relation_extraction() -> None:
    """Requirement 7 (zero-paid invariant), тот же класс проверки, что
    `test_extraction_never_leaves_the_machine` (semantic_extract.py) —
    здесь она исполняется ЖИВЬЁМ как gate внутри acceptance, не только
    как pytest, чтобы факт попал в сам `R4_FINAL_ACCEPTANCE.json`, а не
    только в CI-лог отдельного прогона тестов."""
    tree = ast.parse(inspect.getsource(semantic_extract_module))
    urls = [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "://" in node.value]
    if urls != [semantic_extract_module.OLLAMA_URL]:
        raise RuntimeError(f"semantic_extract.py ссылается на посторонние адреса: {urls}")
    if not semantic_extract_module.OLLAMA_URL.startswith("http://ollama:"):
        raise RuntimeError(f"OLLAMA_URL не локальный: {semantic_extract_module.OLLAMA_URL!r}")


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class R4FinalAcceptance:
    run_id: str
    generated_at_utc: str
    git_sha: str
    model: str
    model_digest: str
    keep_alive: str | None
    golden_corpus_sha256: str
    benchmark_harness_sha256: str
    compiler_revision_sha256: str
    gate_spec_revision: str
    fingerprint_hash: str | None
    compiler_active: bool
    proposed_edges_total: int
    compiled_edges_total: int
    scored_edges_total: int
    litellm_calls: int
    openrouter_calls: int
    checks: list[GateCheck] = field(default_factory=list)
    hard_gate_passed: bool | None = None
    hard_gate_violations: list[str] = field(default_factory=list)
    resources: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    schema_stats: dict = field(default_factory=dict)
    overall_pass: bool = False
    error: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_acceptance(*, report: GoldenBenchmarkReport, resources: ResourceStats,
                     litellm_calls: int, openrouter_calls: int, git_sha: str,
                     model_digest: str, fingerprint_hash: str | None,
                     run_id: str | None = None) -> R4FinalAcceptance:
    """Строит `R4FinalAcceptance`. Никогда не поднимает исключение сама
    (см. `_cli_evaluate` — вызывающий код обязан ловить и превращать в
    FAIL-артефакт, requirement 8: «evaluator exception -> FAIL», а не
    отсутствие артефакта вовсе); все внутренние проверки, способные
    упасть (`verify_*`), вызываются явно и их исключения пробрасываются
    отсюда наверх намеренно — `_cli_evaluate` их ловит."""
    checks: list[GateCheck] = []

    def _run_check(name: str, fn) -> None:
        try:
            fn()
            checks.append(GateCheck(name=name, passed=True))
        except Exception as exc:  # noqa: BLE001 — каждая ошибка здесь обязана стать FAIL, не пропасть
            checks.append(GateCheck(name=name, passed=False, detail=str(exc)))

    _run_check("compiler_is_sole_edge_source", verify_compiler_is_sole_edge_source)
    _run_check("zero_cloud_relation_extraction", verify_zero_cloud_relation_extraction)

    proposed_edges_total = sum(r.proposed_edges_count for r in report.runs)
    compiled_edges_total = sum(r.compiled_edges_count for r in report.runs)
    structural_checks_passed = all(c.passed for c in checks)

    candidate = CandidateResult(
        model=report.model, quant_tag=model_digest, golden=report, resources=resources,
        litellm_calls=litellm_calls, openrouter_calls=openrouter_calls)
    gate = evaluate_hard_gates(candidate)

    if report.metrics.cases_scored == 0:
        # Requirement 4: пустой/вырожденный прогон — не «0 нарушений»,
        # отдельный явный provenance-гейт, не выводимый из остальных
        # (см. semantic_benchmark_selection.py про «идеальная точность
        # через молчание» — та же ловушка на уровне всего прогона).
        checks.append(GateCheck(
            name="non_vacuous_run", passed=False,
            detail="metrics.cases_scored == 0 — ни один кейс не дал CaseScore"))
    else:
        checks.append(GateCheck(name="non_vacuous_run", passed=True))

    all_checks_passed = all(c.passed for c in checks)
    overall_pass = bool(all_checks_passed and gate.passed and structural_checks_passed)

    return R4FinalAcceptance(
        run_id=run_id or uuid.uuid4().hex,
        generated_at_utc=_utcnow_iso(),
        git_sha=git_sha,
        model=report.model,
        model_digest=model_digest,
        keep_alive=report.keep_alive,
        golden_corpus_sha256=semantic_benchmark_module._sha256_of_module_source(
            semantic_benchmark_fixtures_module),
        benchmark_harness_sha256=semantic_benchmark_module._sha256_of_module_source(
            semantic_benchmark_module),
        compiler_revision_sha256=semantic_benchmark_module._sha256_of_module_source(
            relation_compiler_module),
        gate_spec_revision=GATE_SPEC_REVISION,
        fingerprint_hash=fingerprint_hash,
        compiler_active=structural_checks_passed,
        proposed_edges_total=proposed_edges_total,
        compiled_edges_total=compiled_edges_total,
        scored_edges_total=compiled_edges_total,
        litellm_calls=litellm_calls,
        openrouter_calls=openrouter_calls,
        checks=checks,
        hard_gate_passed=gate.passed,
        hard_gate_violations=gate.violations,
        resources=_resources_to_dict(resources),
        metrics=_metrics_to_dict(report),
        schema_stats=_schema_stats_to_dict(report),
        overall_pass=overall_pass,
    )


def _resources_to_dict(resources: ResourceStats) -> dict:
    return dataclasses.asdict(resources)


def _metrics_to_dict(report: GoldenBenchmarkReport) -> dict:
    return dataclasses.asdict(report.metrics)


def _schema_stats_to_dict(report: GoldenBenchmarkReport) -> dict:
    data = dataclasses.asdict(report.schema_stats)
    data["first_pass_rate"] = report.schema_stats.first_pass_rate
    data["avg_repair_attempts"] = report.schema_stats.avg_repair_attempts
    data["processed_window_coverage"] = report.schema_stats.processed_window_coverage
    return data


def acceptance_to_dict(acceptance: R4FinalAcceptance) -> dict:
    return dataclasses.asdict(acceptance)


def _failure_artifact(*, error: str, git_sha: str, model: str, model_digest: str,
                      run_id: str | None) -> R4FinalAcceptance:
    """Requirement 8/12: исключение внутри evaluator/compiler — FAIL с
    сохранённым артефактом, не голый traceback без JSON для диагностики."""
    return R4FinalAcceptance(
        run_id=run_id or uuid.uuid4().hex, generated_at_utc=_utcnow_iso(), git_sha=git_sha,
        model=model, model_digest=model_digest, keep_alive=None,
        golden_corpus_sha256="", benchmark_harness_sha256="", compiler_revision_sha256="",
        gate_spec_revision=GATE_SPEC_REVISION, fingerprint_hash=None, compiler_active=False,
        proposed_edges_total=0, compiled_edges_total=0, scored_edges_total=0,
        litellm_calls=0, openrouter_calls=0, overall_pass=False, error=error)


def _cli_evaluate(args: argparse.Namespace) -> None:
    try:
        if args.combined:
            # Контейнер, где реально живёт `helm_core`, не монтирует
            # BASE_DIR бенчмарка (та же причина, что у
            # `r4-evaluate-hard-gates.sh`) — result.json и
            # resources-<model>.json обязаны попасть внутрь ОДНИМ
            # потоком (stdin), а не двумя раздельными путями. Один файл
            # с {"result": ..., "resources": ...} читается ОТСЮДА, не
            # копированием исходной логики чтения result.json/resources
            # заново — сама распаковка ниже идентична отдельным веткам.
            with open(args.combined, encoding="utf-8") as f:
                combined = json.load(f)
            result_data, resources_data = combined["result"], combined["resources"]
        else:
            with open(args.result, encoding="utf-8") as f:
                result_data = json.load(f)
            with open(args.resources, encoding="utf-8") as f:
                resources_data = json.load(f)

        report = golden_report_from_dict(result_data)
        fingerprint_hash = (result_data.get("fingerprint") or {}).get("fingerprint_hash")
        resources = ResourceStats(**{k: v for k, v in resources_data.items()
                                     if k in ResourceStats.__dataclass_fields__})

        acceptance = build_acceptance(
            report=report, resources=resources, litellm_calls=args.litellm_calls,
            openrouter_calls=args.openrouter_calls, git_sha=args.git_sha,
            model_digest=args.model_digest, fingerprint_hash=fingerprint_hash, run_id=args.run_id)
    except Exception as exc:  # noqa: BLE001 — requirement 8: любая ошибка evaluator -> FAIL-артефакт, не crash без JSON
        acceptance = _failure_artifact(
            error=f"{type(exc).__name__}: {exc}", git_sha=args.git_sha, model=args.model or "",
            model_digest=args.model_digest, run_id=args.run_id)

    print(json.dumps(acceptance_to_dict(acceptance), ensure_ascii=False, indent=2))
    sys.exit(0 if acceptance.overall_pass else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    ev = sub.add_parser("evaluate", help="R4.7 final acceptance: строгий AND всех §14.18 hard gates")
    ev.add_argument("--result", default=None, help="result.json уже провалидированного golden-прогона")
    ev.add_argument("--resources", default=None, help="resources-<model>.json (ResourceStats)")
    ev.add_argument("--combined", default=None,
                    help="Путь (можно /dev/stdin) к JSON {'result':..., 'resources':...} — "
                         "альтернатива --result/--resources одним потоком")
    ev.add_argument("--litellm-calls", type=int, required=True)
    ev.add_argument("--openrouter-calls", type=int, required=True)
    ev.add_argument("--git-sha", required=True)
    ev.add_argument("--model-digest", required=True)
    ev.add_argument("--model", default=None, help="Только для failure-артефакта, если --result не читается")
    ev.add_argument("--run-id", default=None)

    args = parser.parse_args()
    if args.mode == "evaluate":
        has_combined = args.combined is not None
        has_separate = args.result is not None or args.resources is not None
        if has_combined == has_separate:
            parser.error(
                "evaluate: укажите РОВНО ОДИН способ входных данных — либо --combined, "
                "либо оба --result и --resources")
        if has_separate and (args.result is None or args.resources is None):
            parser.error("evaluate: --result и --resources обязаны быть указаны вместе")
        _cli_evaluate(args)


if __name__ == "__main__":
    main()
