"""R4.7 final acceptance (владелец 04.09.2026) — единственный live прогон
не должен быть первым исполнением control-flow: PASS fixture, отдельный
FAIL на каждый §14.18 hard gate, missing-metric, compiler-inactive и
evaluator-exception обязаны быть проверены здесь, офлайн, до
workflow_dispatch (requirement 8)."""

from __future__ import annotations

import json

import pytest

from helm_core.knowledge import r4_final_acceptance as r4fa
from helm_core.knowledge.semantic_benchmark import CaseRun, GoldenBenchmarkReport, SchemaStats
from helm_core.knowledge.semantic_benchmark_metrics import AggregateMetrics, CaseScore
from helm_core.knowledge.semantic_benchmark_selection import ResourceStats


def _passing_resources() -> ResourceStats:
    return ResourceStats(
        cold_latency_seconds=10.0, warm_latency_seconds=5.0, total_benchmark_seconds=120.0,
        peak_rss_mb=200.0, peak_cpu_percent=50.0, swap_before_mb=0.0, swap_peak_mb=0.0,
        swap_after_mb=0.0, oom_occurred=False, other_services_degraded=False, keep_alive_policy="0")


def _passing_metrics(**overrides) -> AggregateMetrics:
    base = dict(
        cases_scored=21, relation_gold_scoreable=10, relation_extracted_total=10,
        relation_precision=0.95, relation_recall=0.90, critical_entity_event_recall=0.95,
        total_material_hallucinations=0, exact_identifier_corruption=0, exact_date_corruption=0,
    )
    base.update(overrides)
    return AggregateMetrics(**base)


def _passing_schema_stats(**overrides) -> SchemaStats:
    base = dict(cases_total=21, first_pass_success=21, failed_cases=0, truncated_cases=0,
               malformed_results=0, total_repair_attempts=0)
    base.update(overrides)
    return SchemaStats(**base)


def _passing_report(*, metrics=None, schema_stats=None, runs=None) -> GoldenBenchmarkReport:
    runs = runs if runs is not None else [
        CaseRun(case_id="c1", categories=(), outcome="processed", latency_seconds=1.0,
               repair_attempts=0, score=CaseScore(case_id="c1", categories=(), is_safety_case=False),
               proposed_edges_count=5, compiled_edges_count=3),
    ]
    return GoldenBenchmarkReport(
        model="qwen2.5:7b", keep_alive="0", runs=runs,
        schema_stats=schema_stats or _passing_schema_stats(),
        stability=[], metrics=metrics or _passing_metrics(), latencies_seconds=[1.0])


def _build(*, report=None, resources=None, litellm_calls=0, openrouter_calls=0):
    return r4fa.build_acceptance(
        report=report or _passing_report(), resources=resources or _passing_resources(),
        litellm_calls=litellm_calls, openrouter_calls=openrouter_calls,
        git_sha="deadbeef", model_digest="sha256:abc", fingerprint_hash="fp-1", run_id="test-run")


class TestPassFixture:
    def test_all_green_yields_overall_pass(self):
        acc = _build()
        assert acc.overall_pass is True
        assert acc.hard_gate_passed is True
        assert acc.hard_gate_violations == []
        assert all(c.passed for c in acc.checks)

    def test_pass_fixture_serializes_to_json(self):
        acc = _build()
        data = r4fa.acceptance_to_dict(acc)
        json.dumps(data)  # не должно бросать — весь артефакт JSON-сериализуем
        assert data["overall_pass"] is True
        assert data["gate_spec_revision"] == r4fa.GATE_SPEC_REVISION


class TestEachHardGateCanFailAlone:
    """Владелец requirement 4/8: отдельный тест на FAIL каждого §14.18
    hard gate — не один общий "что-то не так", а по одному на гейт."""

    def test_processed_window_coverage_below_100_fails(self):
        acc = _build(report=_passing_report(schema_stats=_passing_schema_stats(failed_cases=1)))
        assert acc.overall_pass is False
        assert any("coverage" in v for v in acc.hard_gate_violations)

    def test_schema_invalid_terminal_window_fails(self):
        acc = _build(report=_passing_report(schema_stats=_passing_schema_stats(malformed_results=1)))
        assert acc.overall_pass is False
        assert any("schema-invalid" in v for v in acc.hard_gate_violations)

    def test_unsupported_critical_facts_fails(self):
        acc = _build(report=_passing_report(metrics=_passing_metrics(total_material_hallucinations=1)))
        assert acc.overall_pass is False
        assert any("unsupported critical facts" in v for v in acc.hard_gate_violations)

    def test_exact_identifier_corruption_fails(self):
        acc = _build(report=_passing_report(metrics=_passing_metrics(exact_identifier_corruption=1)))
        assert acc.overall_pass is False
        assert any("identifier corruption" in v for v in acc.hard_gate_violations)

    def test_exact_date_corruption_fails(self):
        acc = _build(report=_passing_report(metrics=_passing_metrics(exact_date_corruption=1)))
        assert acc.overall_pass is False
        assert any("date corruption" in v for v in acc.hard_gate_violations)

    def test_critical_entity_event_recall_below_90_fails(self):
        acc = _build(report=_passing_report(metrics=_passing_metrics(critical_entity_event_recall=0.5)))
        assert acc.overall_pass is False
        assert any("critical expected entity/event recall" in v for v in acc.hard_gate_violations)

    def test_relation_precision_below_90_fails(self):
        acc = _build(report=_passing_report(metrics=_passing_metrics(relation_precision=0.5)))
        assert acc.overall_pass is False
        assert any("relation precision" in v for v in acc.hard_gate_violations)

    def test_relation_silence_is_not_free_precision(self):
        """R4.6.B.1: gold ожидал связи, извлечено 0 — молчание не
        засчитывается за точность, отдельная явная проверка."""
        acc = _build(report=_passing_report(
            metrics=_passing_metrics(relation_gold_scoreable=10, relation_extracted_total=0)))
        assert acc.overall_pass is False
        assert any("молчание не засчитывается" in v for v in acc.hard_gate_violations)


class TestMissingMetricIsFail:
    def test_missing_resource_field_fails(self):
        resources = _passing_resources()
        resources.peak_rss_mb = None
        acc = _build(resources=resources)
        assert acc.overall_pass is False
        assert any("отсутствуют" in v for v in acc.hard_gate_violations)

    def test_vacuous_run_with_zero_cases_scored_fails(self):
        acc = _build(report=_passing_report(metrics=_passing_metrics(cases_scored=0)))
        assert acc.overall_pass is False
        assert any(c.name == "non_vacuous_run" and not c.passed for c in acc.checks)


class TestCompilerInactiveIsFail:
    def test_broken_compiler_provenance_proof_fails_whole_run(self, monkeypatch):
        def _boom():
            raise RuntimeError("simulated: extraction.edges no longer traced to compile_relations()")

        monkeypatch.setattr(r4fa, "verify_compiler_is_sole_edge_source", _boom)
        acc = _build()
        assert acc.overall_pass is False
        assert acc.compiler_active is False
        failed = [c for c in acc.checks if c.name == "compiler_is_sole_edge_source"]
        assert failed and not failed[0].passed
        assert "simulated" in failed[0].detail

    def test_broken_zero_cloud_proof_fails_whole_run(self, monkeypatch):
        def _boom():
            raise RuntimeError("simulated: semantic_extract.py now references an external host")

        monkeypatch.setattr(r4fa, "verify_zero_cloud_relation_extraction", _boom)
        acc = _build()
        assert acc.overall_pass is False
        failed = [c for c in acc.checks if c.name == "zero_cloud_relation_extraction"]
        assert failed and not failed[0].passed

    def test_real_structural_proofs_pass_on_current_codebase(self):
        """Не мок — сама текущая кодовая база обязана проходить оба
        структурных доказательства, иначе R4.7 wiring регрессировал."""
        r4fa.verify_compiler_is_sole_edge_source()
        r4fa.verify_zero_cloud_relation_extraction()


class TestEvaluatorExceptionIsFail:
    def test_missing_result_file_yields_fail_artifact_not_crash(self, tmp_path, capsys):
        import argparse

        args = argparse.Namespace(
            result=str(tmp_path / "does-not-exist.json"), resources=str(tmp_path / "res.json"),
            combined=None, litellm_calls=0, openrouter_calls=0, git_sha="deadbeef",
            model_digest="sha256:abc", model="qwen2.5:7b", run_id="test-run")
        with pytest.raises(SystemExit) as exc_info:
            r4fa._cli_evaluate(args)
        assert exc_info.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["overall_pass"] is False
        assert out["error"] is not None

    def test_malformed_result_json_yields_fail_artifact_not_crash(self, tmp_path, capsys):
        import argparse

        result_path = tmp_path / "result.json"
        result_path.write_text("{not valid json", encoding="utf-8")
        args = argparse.Namespace(
            result=str(result_path), resources=str(tmp_path / "res.json"),
            combined=None, litellm_calls=0, openrouter_calls=0, git_sha="deadbeef",
            model_digest="sha256:abc", model="qwen2.5:7b", run_id="test-run")
        with pytest.raises(SystemExit) as exc_info:
            r4fa._cli_evaluate(args)
        assert exc_info.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["overall_pass"] is False
        assert out["error"] is not None


class TestCliArgumentValidation:
    def test_main_evaluate_without_required_flags_exits_nonzero(self, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "argv", ["r4_final_acceptance", "evaluate"])
        with pytest.raises(SystemExit) as exc_info:
            r4fa.main()
        assert exc_info.value.code != 0

    def test_unknown_mode_rejected_by_argparse(self, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "argv", ["r4_final_acceptance", "not-a-real-mode"])
        with pytest.raises(SystemExit):
            r4fa.main()

    def test_combined_and_separate_together_rejected(self, tmp_path, monkeypatch, capsys):
        import sys
        combined = tmp_path / "combined.json"
        combined.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", [
            "r4_final_acceptance", "evaluate", "--combined", str(combined),
            "--result", str(tmp_path / "r.json"), "--resources", str(tmp_path / "res.json"),
            "--litellm-calls", "0", "--openrouter-calls", "0",
            "--git-sha", "x", "--model-digest", "y"])
        with pytest.raises(SystemExit) as exc_info:
            r4fa.main()
        assert exc_info.value.code == 2
        assert "РОВНО ОДИН" in capsys.readouterr().err

    def test_neither_combined_nor_separate_rejected(self, monkeypatch, capsys):
        import sys
        monkeypatch.setattr(sys, "argv", [
            "r4_final_acceptance", "evaluate", "--litellm-calls", "0", "--openrouter-calls", "0",
            "--git-sha", "x", "--model-digest", "y"])
        with pytest.raises(SystemExit) as exc_info:
            r4fa.main()
        assert exc_info.value.code == 2

    def test_result_without_resources_rejected(self, tmp_path, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "argv", [
            "r4_final_acceptance", "evaluate", "--result", str(tmp_path / "r.json"),
            "--litellm-calls", "0", "--openrouter-calls", "0", "--git-sha", "x", "--model-digest", "y"])
        with pytest.raises(SystemExit) as exc_info:
            r4fa.main()
        assert exc_info.value.code == 2


class TestCombinedInputMode:
    def test_combined_file_produces_same_result_as_separate_files(self, tmp_path):
        report = _passing_report()
        resources = _passing_resources()
        import dataclasses
        from helm_core.knowledge.semantic_benchmark import golden_report_to_dict
        result_data = golden_report_to_dict(report)
        resources_data = dataclasses.asdict(resources)

        combined_path = tmp_path / "combined.json"
        combined_path.write_text(json.dumps({"result": result_data, "resources": resources_data}),
                                 encoding="utf-8")

        import argparse
        args = argparse.Namespace(
            result=None, resources=None, combined=str(combined_path),
            litellm_calls=0, openrouter_calls=0, git_sha="deadbeef", model_digest="sha256:abc",
            model=None, run_id="test-run")
        with pytest.raises(SystemExit) as exc_info:
            r4fa._cli_evaluate(args)
        assert exc_info.value.code == 0


class TestLitellmOpenrouterCallsAreGated:
    def test_nonzero_litellm_calls_fails(self):
        acc = _build(litellm_calls=1)
        assert acc.overall_pass is False
        assert any("LiteLLM" in v for v in acc.hard_gate_violations)

    def test_nonzero_openrouter_calls_fails(self):
        acc = _build(openrouter_calls=1)
        assert acc.overall_pass is False
        assert any("OpenRouter" in v for v in acc.hard_gate_violations)


def test_provenance_counts_reflect_case_runs_not_scoring_internals():
    runs = [
        CaseRun(case_id="c1", categories=(), outcome="processed", latency_seconds=1.0,
               repair_attempts=0, score=CaseScore(case_id="c1", categories=(), is_safety_case=False),
               proposed_edges_count=7, compiled_edges_count=4),
        CaseRun(case_id="c2", categories=(), outcome="processed", latency_seconds=1.0,
               repair_attempts=0, score=CaseScore(case_id="c2", categories=(), is_safety_case=False),
               proposed_edges_count=2, compiled_edges_count=2),
    ]
    acc = _build(report=_passing_report(runs=runs))
    assert acc.proposed_edges_total == 9
    assert acc.compiled_edges_total == 6
    assert acc.scored_edges_total == acc.compiled_edges_total
