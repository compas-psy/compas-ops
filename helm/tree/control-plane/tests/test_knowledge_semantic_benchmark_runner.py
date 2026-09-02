"""R4 (§14.18) — харнесс проверяется на поддельном извлекателе: сначала
идеальный ответ на каждую фикстуру, потом порченые случаи (провал схемы,
переполнение, нестабильность) — каждый должен быть виден в отчёте, а не
тонуть в общем зачёте. Shadow-прогон проверяется отдельно на то, что он
СТРУКТУРНО не может унести сырой текст источника или ответа модели —
владелец запретил это прямо (R4 п.2Б)."""

from __future__ import annotations

import dataclasses

import json

from helm_core.knowledge.semantic_benchmark import (
    ShadowWindowSample, run_golden_benchmark, run_shadow_benchmark,
)
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES, GoldenCase
from helm_core.knowledge.semantic_extract import (
    ExtractedAtom, ExtractedEdge, ExtractedEntity, ExtractionFailed, WindowExtraction,
    WindowTruncated, extract_window,
)

_BY_ID = {c.case_id: c for c in GOLDEN_CASES}


def _perfect_extraction(case: GoldenCase) -> WindowExtraction:
    """Ref фикстуры используется как local_id — сопоставлению всё равно,
    какое имя выбрала модель, важно только содержимое."""
    return WindowExtraction(
        entities=[ExtractedEntity(local_id=e.ref, entity_type=e.entity_type, label=e.label,
                                  subtype=e.subtype, aliases=e.aliases) for e in case.entities],
        atoms=[ExtractedAtom(local_id=a.ref, kind=a.kind, title=a.canonical_text[:40],
                             text=a.canonical_text, subtype=a.subtype, occurred_at=a.occurred_at,
                             date_precision=a.date_precision) for a in case.atoms],
        edges=[ExtractedEdge(from_local_id=e.from_ref, relation_type=e.relation_type,
                             to_local_id=e.to_ref, role=e.role) for e in case.edges],
    )


def test_perfect_extractor_yields_zero_hallucinations_and_full_schema_success():
    def fake_extract(text, *, domain, heading_path=(), model, keep_alive=None):
        case = next(c for c in GOLDEN_CASES if c.text == text)
        return _perfect_extraction(case)

    report = run_golden_benchmark(model="fake", extract_fn=fake_extract, stability_repeats=2)
    assert report.schema_stats.failed_cases == 0
    assert report.schema_stats.truncated_cases == 0
    assert report.schema_stats.malformed_results == 0
    assert report.schema_stats.first_pass_success == len(GOLDEN_CASES)
    assert report.metrics.total_material_hallucinations == 0
    assert report.metrics.entity_recall == 1.0
    assert all(s.reproducible for s in report.stability)


def test_failed_case_is_isolated_and_does_not_break_the_rest():
    def fake_extract(text, *, domain, heading_path=(), model, keep_alive=None):
        case = next(c for c in GOLDEN_CASES if c.text == text)
        if case.case_id == "doctor_visit":
            raise ExtractionFailed("подделанный сбой для теста")
        return _perfect_extraction(case)

    report = run_golden_benchmark(model="fake", extract_fn=fake_extract, stability_repeats=1)
    assert report.schema_stats.failed_cases == 1
    failed_run = next(r for r in report.runs if r.case_id == "doctor_visit")
    assert failed_run.outcome == "failed" and failed_run.score is None
    # Остальные кейсы не пострадали от одного провала.
    assert report.metrics.cases_scored == len(GOLDEN_CASES) - 1


def test_truncated_case_is_recorded_separately_from_failure():
    def fake_extract(text, *, domain, heading_path=(), model, keep_alive=None):
        case = next(c for c in GOLDEN_CASES if c.text == text)
        if case.case_id == "long_dense_window":
            raise WindowTruncated("подделанное переполнение для теста")
        return _perfect_extraction(case)

    report = run_golden_benchmark(model="fake", extract_fn=fake_extract, stability_repeats=1)
    assert report.schema_stats.truncated_cases == 1
    assert report.schema_stats.failed_cases == 0
    run = next(r for r in report.runs if r.case_id == "long_dense_window")
    assert run.outcome == "truncated"


def test_unstable_model_is_flagged_not_averaged_away():
    call_count = {"doctor_visit": 0}

    def flaky_extract(text, *, domain, heading_path=(), model, keep_alive=None):
        case = next(c for c in GOLDEN_CASES if c.text == text)
        if case.case_id == "doctor_visit":
            call_count["doctor_visit"] += 1
            label = f"Кириченко {call_count['doctor_visit']}"  # меняется на каждый вызов
            return WindowExtraction(entities=[
                ExtractedEntity(local_id="e1", entity_type="PERSON", label=label)])
        return _perfect_extraction(case)

    report = run_golden_benchmark(model="fake", extract_fn=flaky_extract, stability_repeats=3)
    unstable = next(s for s in report.stability if s.case_id == "doctor_visit")
    assert not unstable.reproducible
    assert unstable.identical_runs < unstable.runs


def test_shadow_benchmark_result_carries_no_raw_text_or_model_output():
    sensitive_label = "Секретный Пациент Тестовый"
    sample = ShadowWindowSample(source_id="src-1", domain="health", window_ordinal=0,
                                text=f"Приём провёл врач для {sensitive_label}.")

    def fake_extract(text, *, domain, heading_path=(), model, keep_alive=None):
        return WindowExtraction(
            entities=[ExtractedEntity(local_id="e1", entity_type="PERSON", label=sensitive_label)],
            atoms=[ExtractedAtom(local_id="a1", kind="event", title="Приём",
                                 text=f"Приём провёл врач для {sensitive_label}.")])

    report = run_shadow_benchmark([sample], model="fake", extract_fn=fake_extract)
    assert report.windows_total == 1
    result = report.results[0]
    for f in dataclasses.fields(result):
        value = str(getattr(result, f.name))
        assert sensitive_label not in value, f"поле {f.name} унесло текст источника/ответа модели"
    assert result.window_hash == sample.window_hash
    assert result.entities_count == 1 and result.atoms_count == 1


def test_first_pass_vs_repaired_success_are_not_conflated(monkeypatch):
    """Через ПОДДЕЛЬНЫЙ extract_fn харнесс никогда не увидит починку — она
    происходит внутри `extract_window()`. Здесь используется настоящий
    `extract_window` с подменённым `_call_ollama`, чтобы одна фикстура
    ответила невалидным JSON на первой попытке и валидным — на второй:
    именно эта разница (0 против 1 попытки починки) раньше терялась
    условием `repair_attempts <= 1`, засчитывавшим и то, и другое как
    «с первого раза»."""
    import helm_core.knowledge.semantic_extract as module

    case = next(c for c in GOLDEN_CASES if c.case_id == "fact_plain")
    good_payload = json.dumps({
        "entities": [], "edges": [],
        "atoms": [{"local_id": "a1", "kind": "fact", "title": "Курс доллара",
                  "text": case.atoms[0].canonical_text}],
    })
    calls = {"n": 0}

    def flaky_call_ollama(prompt, *, model, keep_alive=None):
        calls["n"] += 1
        return "не json" if calls["n"] == 1 else good_payload

    monkeypatch.setattr(module, "_call_ollama", flaky_call_ollama)

    report = run_golden_benchmark(model="gemma2:2b", extract_fn=extract_window,
                                  stability_repeats=1, cases=(case,))
    assert report.schema_stats.first_pass_success == 0
    assert report.schema_stats.repaired_success == 1
    assert report.runs[0].repair_attempts == 1


def test_shadow_benchmark_domain_breakdown_and_failure_counts():
    samples = [
        ShadowWindowSample(source_id="s1", domain="health", window_ordinal=0, text="текст 1"),
        ShadowWindowSample(source_id="s2", domain="work", window_ordinal=0, text="текст 2"),
        ShadowWindowSample(source_id="s3", domain="work", window_ordinal=1, text="текст 3"),
    ]

    def fake_extract(text, *, domain, heading_path=(), model, keep_alive=None):
        if text == "текст 2":
            raise ExtractionFailed("сбой")
        return WindowExtraction()

    report = run_shadow_benchmark(samples, model="fake", extract_fn=fake_extract)
    assert report.by_domain == {"health": 1, "work": 2}
    assert report.failed_count == 1
    assert report.no_knowledge_count == 2
