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


def test_raw_diagnostics_capture_entities_atoms_edges_and_rejected_per_case():
    """P7 (владелец 2026-09-04) — synthetic-only raw diagnostics: передан
    пустой список -> получаем per-case entities/atoms/compiled_edges,
    не только агрегатные счётчики `GoldenBenchmarkReport`."""
    def fake_extract(text, *, domain, heading_path=(), model, keep_alive=None):
        case = next(c for c in GOLDEN_CASES if c.text == text)
        return _perfect_extraction(case)

    diagnostics: list[dict] = []
    case = _BY_ID["doctor_visit"]
    run_golden_benchmark(model="fake", extract_fn=fake_extract, stability_repeats=1,
                         cases=(case,), raw_diagnostics=diagnostics)
    assert len(diagnostics) == 1
    entry = diagnostics[0]
    assert entry["case_id"] == "doctor_visit"
    assert entry["entities"] and entry["entities"][0]["label"] == "Кириченко Сергей Александрович"
    assert entry["atoms"]
    assert entry["compiled_edges"]
    assert entry["rejected"] == []
    assert entry["split_lineage"] == []


def test_raw_diagnostics_default_to_none_and_add_no_overhead():
    """По умолчанию (без `raw_diagnostics=`) поведение не меняется —
    P7 строго opt-in."""
    def fake_extract(text, *, domain, heading_path=(), model, keep_alive=None):
        case = next(c for c in GOLDEN_CASES if c.text == text)
        return _perfect_extraction(case)

    report = run_golden_benchmark(model="fake", extract_fn=fake_extract, stability_repeats=1,
                                  cases=(_BY_ID["doctor_visit"],))
    assert report.schema_stats.cases_total == 1


def test_run_shadow_benchmark_has_no_raw_diagnostics_parameter():
    """P7: для реального пользовательского корпуса raw diagnostics не
    собираются НИКОГДА — проверяется структурно (сигнатура функции), не
    «по умолчанию выключено», чтобы это нельзя было случайно включить."""
    import inspect

    assert "raw_diagnostics" not in inspect.signature(run_shadow_benchmark).parameters


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


def test_cli_golden_prints_valid_json_report(monkeypatch, capsys):
    """CLI — то, что реально дёрнет живой скрипт через `docker compose
    exec`. Проверяется на подделанном `_call_ollama`, чтобы не зависеть
    от Ollama локально; сам живой прогон живёт в scripts/, не здесь."""
    import helm_core.knowledge.semantic_benchmark as module
    import helm_core.knowledge.semantic_extract as extract_module

    def fake_call_ollama(prompt, *, model, keep_alive=None, **kwargs):
        return json.dumps({"entities": [], "atoms": [], "edges": []})

    monkeypatch.setattr(extract_module, "_call_ollama", fake_call_ollama)
    monkeypatch.setattr(
        "sys.argv",
        ["semantic_benchmark", "golden", "--model", "fake-model",
         "--case", "no_knowledge", "--stability-repeats", "1"],
    )
    module.main()
    printed = json.loads(capsys.readouterr().out)
    assert printed["model"] == "fake-model"
    assert printed["schema_stats"]["cases_total"] == 1
    assert printed["metrics"]["cases_scored"] == 1


def test_cli_golden_writes_raw_diagnostics_file_when_requested(monkeypatch, capsys, tmp_path):
    """P7 (владелец 2026-09-04): `--raw-diagnostics-out` пишет отдельный
    файл с сырыми entities/atoms/compiled_edges, не смешивая его с
    печатаемым (aggregate-only) report'ом."""
    import helm_core.knowledge.semantic_benchmark as module
    import helm_core.knowledge.semantic_extract as extract_module

    def fake_call_ollama(prompt, *, model, keep_alive=None, **kwargs):
        # evidence_quote обязан быть дословной подстрокой окна
        # (`no_knowledge`: «Документ сформирован автоматически...») —
        # иначе `validate()` отбросит сущность как негрундированную.
        return json.dumps({
            "entities": [{"local_id": "e1", "entity_type": "ORGANIZATION", "label": "Документ",
                         "evidence_quote": "Документ"}],
            "atoms": [],
        })

    out_path = tmp_path / "raw.json"
    monkeypatch.setattr(extract_module, "_call_ollama", fake_call_ollama)
    monkeypatch.setattr(
        "sys.argv",
        ["semantic_benchmark", "golden", "--model", "fake-model",
         "--case", "no_knowledge", "--stability-repeats", "1",
         "--raw-diagnostics-out", str(out_path)],
    )
    module.main()
    printed = json.loads(capsys.readouterr().out)
    assert "raw_diagnostics" not in printed, "report остаётся aggregate-only"

    diagnostics = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(diagnostics) == 1
    assert diagnostics[0]["case_id"] == "no_knowledge"
    assert diagnostics[0]["entities"][0]["label"] == "Документ"


def test_golden_report_json_round_trip_survives_a_full_run():
    """`golden_report_from_dict` разбирает то, что сервер напечатал через
    CLI, ВНЕ сервера — выбор winner не должен требовать ещё одного
    обращения к Ollama. Прогон на всех фикстурах, не на одной — чтобы
    поймать поле, забытое в сериализации именно на непустом отчёте."""
    from helm_core.knowledge.semantic_benchmark import (
        golden_report_from_dict, golden_report_to_dict,
    )

    def fake_extract(text, *, domain, heading_path=(), model, keep_alive=None):
        case = next(c for c in GOLDEN_CASES if c.text == text)
        return _perfect_extraction(case)

    report = run_golden_benchmark(model="fake", extract_fn=fake_extract, stability_repeats=2)
    restored = golden_report_from_dict(json.loads(json.dumps(golden_report_to_dict(report))))
    assert restored == report


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
