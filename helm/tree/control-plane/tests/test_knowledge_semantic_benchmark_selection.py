"""R4 (§14.18) — hard gates и лексикографический выбор winner на
СИНТЕТИЧЕСКИХ результатах (не живой модели): здесь проверяется сама
политика отбора, а не качество какой-либо конкретной модели."""

from __future__ import annotations

import pytest

from helm_core.knowledge.semantic_benchmark import GoldenBenchmarkReport, SchemaStats
from helm_core.knowledge.semantic_benchmark_metrics import AggregateMetrics
from helm_core.knowledge.semantic_benchmark_selection import (
    REQUIRED_RESOURCE_FIELDS, CandidateResult, ResourceStats, _ranking_key, evaluate_hard_gates,
    select_winner,
)


def _report(*, hallucinations=None, safety_hallucinations=0, fabricated_dates=0,
           no_knowledge_violations=0, precision=1.0, recall=1.0, correctness=1.0,
           relation_precision=None, critical_recall=1.0,
           exact_identifier_corruption=0, exact_date_corruption=0,
           failed_cases=0, truncated_cases=0, malformed_results=0,
           avg_repair_attempts=0.0, stable=True,
           p50_latency=1.0) -> GoldenBenchmarkReport:
    # `total_material_hallucinations` в реальном `aggregate()` всегда >=
    # каждой из своих компонент (safety_case_hallucinations — подмножество,
    # fabricated_dates/no_knowledge_violations — слагаемые). Синтетический
    # хелпер обязан держать тот же инвариант сам — иначе тест мог бы задать
    # `fabricated_dates=1` при `total_material_hallucinations=0`, что живые
    # данные никогда не производят, и гейт (по праву читающий только
    # агрегат) не увидел бы нарушение, которое тест утверждает, что задал.
    if hallucinations is None:
        hallucinations = max(safety_hallucinations, fabricated_dates + no_knowledge_violations)
    metrics = AggregateMetrics(
        cases_scored=21,
        entity_precision=precision, atom_precision=precision,
        relation_precision=precision if relation_precision is None else relation_precision,
        entity_recall=recall, atom_recall=recall, relation_recall=recall,
        subtype_accuracy=correctness, relation_type_accuracy=correctness, date_accuracy=correctness,
        total_material_hallucinations=hallucinations, safety_case_hallucinations=safety_hallucinations,
        fabricated_dates=fabricated_dates, no_knowledge_violations=no_knowledge_violations,
        critical_entity_event_recall=critical_recall,
        exact_identifier_corruption=exact_identifier_corruption,
        exact_date_corruption=exact_date_corruption,
    )
    schema = SchemaStats(cases_total=21, failed_cases=failed_cases,
                         truncated_cases=truncated_cases, malformed_results=malformed_results,
                         total_repair_attempts=round(avg_repair_attempts * 21))
    from helm_core.knowledge.semantic_benchmark import StabilityResult
    reps = 3
    stability = [StabilityResult(case_id="doctor_visit", runs=reps,
                                 identical_runs=reps if stable else reps - 1)]
    return GoldenBenchmarkReport(model="fake", keep_alive="0", runs=[], schema_stats=schema,
                                 stability=stability, metrics=metrics, latencies_seconds=[p50_latency])


def _full_resources(*, peak_rss_mb: float = 1000.0, oom=False, degraded=False) -> ResourceStats:
    """Кандидат по умолчанию — ПОЛНОСТЬЮ измеренный (все обязательные
    ресурсные поля реальны, не None) — иначе каждый тест ниже ловил бы
    только что заведённый гейт «недостающее измерение» вместо того,
    что он на самом деле проверяет."""
    return ResourceStats(
        cold_latency_seconds=2.0, warm_latency_seconds=0.5, total_benchmark_seconds=120.0,
        peak_rss_mb=peak_rss_mb, peak_cpu_percent=50.0,
        swap_before_mb=0.0, swap_peak_mb=0.0, swap_after_mb=0.0,
        oom_occurred=oom, other_services_degraded=degraded, keep_alive_policy="0",
    )


def _candidate(model: str, *, peak_rss_mb: float = 1000.0, oom=False, degraded=False,
              litellm_calls=0, openrouter_calls=0, resources: ResourceStats | None = None,
              **report_kwargs) -> CandidateResult:
    report = _report(**report_kwargs)
    report.model = model
    return CandidateResult(
        model=model, quant_tag="q4_0", golden=report,
        resources=resources or _full_resources(peak_rss_mb=peak_rss_mb, oom=oom, degraded=degraded),
        litellm_calls=litellm_calls, openrouter_calls=openrouter_calls)


def test_clean_candidate_passes_all_gates():
    gate = evaluate_hard_gates(_candidate("clean"))
    assert gate.passed and gate.violations == []


class TestNormativeHardGates:
    """§14.18 «Hard gates for initial golden set» — владелец 03.09.2026
    (R4.5.6): предыдущая версия evaluate_hard_gates() не проверяла ни
    processed-window coverage, ни relation precision, ни critical
    entity/event recall вообще — drift, который позволил qwen2.5:7b
    (failed_cases=2, relation_precision=0.28 в run 210) формально
    «пройти» старый гейт. Каждый тест ниже — минимум, который владелец
    явно потребовал: failed_cases=1, relation_precision=0.89,
    critical_recall=0.89 — все строго ниже нормативного порога."""

    def test_failed_cases_disqualifies_even_with_perfect_everything_else(self):
        gate = evaluate_hard_gates(_candidate("x", failed_cases=1))
        assert not gate.passed
        assert any("failed_cases" in v for v in gate.violations)

    def test_truncated_cases_disqualifies(self):
        gate = evaluate_hard_gates(_candidate("x", truncated_cases=1))
        assert not gate.passed
        assert any("truncated_cases" in v for v in gate.violations)

    def test_relation_precision_just_under_threshold_disqualifies(self):
        gate = evaluate_hard_gates(_candidate("x", relation_precision=0.89))
        assert not gate.passed
        assert any("relation precision" in v for v in gate.violations)

    def test_relation_precision_exactly_at_threshold_passes(self):
        """Граница включительна — 90.0% сам по себе не провал (owner: `>=
        90%`), только строго ниже. Без этого теста откат `< 0.90` на `<=
        0.90` в правке гейта прошёл бы незамеченным."""
        gate = evaluate_hard_gates(_candidate("x", relation_precision=0.90))
        assert gate.passed

    def test_critical_entity_event_recall_just_under_threshold_disqualifies(self):
        gate = evaluate_hard_gates(_candidate("x", critical_recall=0.89))
        assert not gate.passed
        assert any("critical expected entity/event recall" in v for v in gate.violations)

    def test_unsupported_critical_facts_disqualifies(self):
        gate = evaluate_hard_gates(_candidate("x", fabricated_dates=1))
        assert not gate.passed
        assert any("unsupported critical facts" in v for v in gate.violations)

    def test_exact_identifier_corruption_disqualifies(self):
        gate = evaluate_hard_gates(_candidate("x", exact_identifier_corruption=1))
        assert not gate.passed
        assert any("identifier corruption" in v for v in gate.violations)

    def test_exact_date_corruption_disqualifies(self):
        gate = evaluate_hard_gates(_candidate("x", exact_date_corruption=1))
        assert not gate.passed
        assert any("date corruption" in v for v in gate.violations)

    def test_malformed_results_disqualifies_via_both_named_checks(self):
        """Владелец перечислил malformed_results и processed-window
        coverage как ДВЕ отдельные строки гейта — оба обязаны сработать
        на одном и том же входе, не только один из них."""
        gate = evaluate_hard_gates(_candidate("x", malformed_results=1))
        assert not gate.passed
        assert any("malformed_results" in v for v in gate.violations)
        assert any("processed-window coverage" in v for v in gate.violations)


def test_any_single_hard_gate_violation_excludes_the_candidate():
    cases = [
        dict(fabricated_dates=1),
        dict(safety_hallucinations=1),
        dict(no_knowledge_violations=1),
        dict(litellm_calls=1),
        dict(openrouter_calls=1),
        dict(oom=True),
        dict(degraded=True),
    ]
    for kwargs in cases:
        gate = evaluate_hard_gates(_candidate("x", **kwargs))
        assert not gate.passed, f"должен был провалиться: {kwargs}"


def test_no_passing_candidate_gives_no_pass_not_a_best_of_bad():
    candidates = [_candidate("a", fabricated_dates=1), _candidate("b", safety_hallucinations=1)]
    result = select_winner(candidates)
    assert result.winner is None
    assert "NO_PASS" in result.winner_reason
    assert set(result.disqualified) == {"a", "b"}


def test_precision_outranks_recall_lexicographically():
    # precision у обоих >= 0.90 (иначе relation_precision hard gate
    # дисквалифицировал бы низкоprecision-кандидата раньше, чем дело
    # дойдёт до сравнения ranking key, и тест проверял бы дисквалификацию,
    # а не лексикографический порядок).
    high_precision_low_recall = _candidate("precise", precision=0.95, recall=0.70)
    low_precision_high_recall = _candidate("recall_heavy", precision=0.90, recall=0.99)
    result = select_winner([high_precision_low_recall, low_precision_high_recall])
    assert result.winner.model == "precise"


def test_disqualified_candidate_never_wins_even_if_metrics_look_better():
    hallucinating_but_precise = _candidate("bad", precision=1.0, recall=1.0, fabricated_dates=1)
    honest_but_weaker = _candidate("good", precision=0.90, recall=0.85)
    result = select_winner([hallucinating_but_precise, honest_but_weaker])
    assert result.winner.model == "good"
    assert "bad" in result.disqualified


def test_smaller_model_preferred_on_near_tie_by_ram_margin():
    # precision/recall у обоих safely >= 0.90 (relation_precision — hard
    # gate) — «near tie» задаётся через correctness (не гейтится), чтобы
    # тест проверял именно RAM-margin override, а не дисквалификацию.
    big = _candidate("big-7b", precision=0.95, recall=0.95, correctness=0.90, peak_rss_mb=6000.0)
    small = _candidate("small-3b", precision=0.95, recall=0.95, correctness=0.895, peak_rss_mb=3000.0)
    result = select_winner([big, small])
    assert result.winner.model == "small-3b"
    assert "R4 п.9" in result.winner_reason


def test_ram_margin_alone_does_not_override_a_real_quality_gap():
    # much_smaller_but_worse держится >= 0.90 по precision/recall (иначе
    # relation_precision hard gate дисквалифицировал бы его раньше, чем
    # дело дойдёт до сравнения ranking key — тест проверял бы
    # дисквалификацию, а не «RAM не перевешивает реальный разрыв в
    # качестве»); реальный разрыв в качестве задан через correctness.
    clearly_better = _candidate("better", precision=0.98, recall=0.98, correctness=0.98,
                                peak_rss_mb=6000.0)
    much_smaller_but_worse = _candidate("smaller", precision=0.90, recall=0.90, correctness=0.50,
                                        peak_rss_mb=1000.0)
    result = select_winner([clearly_better, much_smaller_but_worse])
    assert result.winner.model == "better"


def test_small_ram_difference_does_not_trigger_the_override():
    leader = _candidate("leader", precision=0.95, recall=0.95, correctness=0.90, peak_rss_mb=3100.0)
    close_second = _candidate("second", precision=0.95, recall=0.95, correctness=0.895,
                              peak_rss_mb=3000.0)
    result = select_winner([leader, close_second])
    assert result.winner.model == "leader"


@pytest.mark.parametrize("missing_field", REQUIRED_RESOURCE_FIELDS)
def test_any_missing_required_resource_field_disqualifies_the_candidate(missing_field):
    """Ретракция владельца 02.09.2026 п.6: недостающее измерение — не
    ноль и не «всё в порядке», а гейт. Проверяется поле за полем: ни
    одно не должно молча сойти за измеренное."""
    resources = _full_resources()
    setattr(resources, missing_field, None)
    gate = evaluate_hard_gates(CandidateResult(
        model="incomplete", quant_tag="q4_0", golden=_report(), resources=resources))
    assert not gate.passed
    assert any(missing_field in v for v in gate.violations)


def test_candidate_missing_peak_rss_never_wins_by_looking_cheapest():
    """Ядро находки владельца: раньше peak_rss_mb=None превращался в
    0.0 внутри _ranking_key — «нет данных» выглядело как «идеально мало
    RAM» и выигрывало бы у честно измеренного тяжёлого кандидата."""
    unmeasured = _candidate("unmeasured", precision=0.99, recall=0.99, correctness=0.99,
                            resources=ResourceStats(peak_rss_mb=None))
    honestly_measured = _candidate("measured", precision=0.90, recall=0.90, correctness=0.80,
                                   peak_rss_mb=5000.0)
    result = select_winner([unmeasured, honestly_measured])
    assert result.winner.model == "measured"
    assert "unmeasured" in result.disqualified
    assert any("peak_rss_mb" in v for v in result.disqualified["unmeasured"].violations)


def test_ranking_key_refuses_to_run_on_an_incompletely_measured_candidate():
    incomplete = _candidate("x", resources=ResourceStats(peak_rss_mb=None))
    with pytest.raises(AssertionError):
        _ranking_key(incomplete)


def test_oom_not_checked_is_not_the_same_as_oom_did_not_happen():
    """`oom_occurred=None` (не проверялось) должно гейтить как
    отсутствующее измерение, а не молчаливо трактоваться как False."""
    resources = _full_resources()
    resources.oom_occurred = None
    gate = evaluate_hard_gates(CandidateResult(
        model="unchecked", quant_tag="q4_0", golden=_report(), resources=resources))
    assert not gate.passed
    assert any("oom_occurred" in v for v in gate.violations)
