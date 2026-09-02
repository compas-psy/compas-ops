"""R4 retraction п.3-4 (владелец 02.09.2026) — fingerprint/manifest и
атомарная валидация результата ПЕРЕД публикацией под финальным именем.

Найдено на живом сервере: без fingerprint старый result.json из
предыдущего прогона мог быть тихо принят за актуальный после правки
кода/промпта/фикстур. Без валидации частично записанный JSON (обрыв
SSH/контейнера на середине) мог сойти за завершённый результат."""

from __future__ import annotations

import json

import pytest

from helm_core.knowledge.semantic_benchmark import (
    compute_fingerprint, golden_report_to_dict, main, run_golden_benchmark,
)
from helm_core.knowledge.semantic_benchmark_fixtures import GOLDEN_CASES
from helm_core.knowledge.semantic_extract import WindowExtraction


def test_fingerprint_is_deterministic_for_identical_inputs():
    fp1 = compute_fingerprint(git_sha="abc", model_tag="gemma2:2b", model_digest="8ccf136fdd52",
                              keep_alive="0", run_id="r1")
    fp2 = compute_fingerprint(git_sha="abc", model_tag="gemma2:2b", model_digest="8ccf136fdd52",
                              keep_alive="0", run_id="r1")
    assert fp1 == fp2


@pytest.mark.parametrize("changed_kwarg", [
    {"git_sha": "different"},
    {"model_tag": "qwen2.5:3b"},
    {"model_digest": "different-digest"},
    {"keep_alive": "5m"},
    {"run_id": "different-run"},
])
def test_fingerprint_hash_changes_when_any_input_changes(changed_kwarg):
    base = dict(git_sha="abc", model_tag="gemma2:2b", model_digest="8ccf136fdd52",
               keep_alive="0", run_id="r1")
    fp1 = compute_fingerprint(**base)
    fp2 = compute_fingerprint(**{**base, **changed_kwarg})
    assert fp1["fingerprint_hash"] != fp2["fingerprint_hash"], (
        f"fingerprint не заметил изменение {changed_kwarg}")


def test_fingerprint_captures_the_actual_deployed_code_hash():
    """Не просто произвольная строка — реальный SHA256 файла на диске.
    Меняя код извлечения, меняем fingerprint без отдельного шага."""
    import helm_core.knowledge.semantic_extract as se

    fp = compute_fingerprint(git_sha="abc", model_tag="gemma2:2b", model_digest="d",
                             keep_alive="0", run_id="r")
    import hashlib
    with open(se.__file__, "rb") as f:
        expected = hashlib.sha256(f.read()).hexdigest()
    assert fp["semantic_extract_sha256"] == expected


def test_cli_golden_embeds_fingerprint_when_git_sha_and_digest_given(monkeypatch, capsys):
    import helm_core.knowledge.semantic_extract as extract_module

    def fake_call_ollama(prompt, *, model, keep_alive=None):
        return json.dumps({"entities": [], "atoms": [], "edges": []})

    monkeypatch.setattr(extract_module, "_call_ollama", fake_call_ollama)
    monkeypatch.setattr("sys.argv", [
        "semantic_benchmark", "golden", "--model", "gemma2:2b", "--case", "no_knowledge",
        "--stability-repeats", "1", "--git-sha", "abc123", "--model-digest", "8ccf136fdd52",
        "--run-id", "test-run",
    ])
    main()
    data = json.loads(capsys.readouterr().out)
    assert data["fingerprint"]["git_sha"] == "abc123"
    assert data["fingerprint"]["model_digest"] == "8ccf136fdd52"
    assert data["fingerprint"]["run_id"] == "test-run"
    assert "fingerprint_hash" in data["fingerprint"]


def test_cli_golden_omits_fingerprint_when_git_sha_not_given(monkeypatch, capsys):
    """Быстрые однокейсовые вызовы (keep_alive-проба) не обязаны нести
    fingerprint — он нужен только у канонического полного прогона."""
    import helm_core.knowledge.semantic_extract as extract_module

    def fake_call_ollama(prompt, *, model, keep_alive=None):
        return json.dumps({"entities": [], "atoms": [], "edges": []})

    monkeypatch.setattr(extract_module, "_call_ollama", fake_call_ollama)
    monkeypatch.setattr("sys.argv", [
        "semantic_benchmark", "golden", "--model", "gemma2:2b", "--case", "no_knowledge",
        "--stability-repeats", "1",
    ])
    main()
    data = json.loads(capsys.readouterr().out)
    assert "fingerprint" not in data


def test_cli_fingerprint_subcommand_never_touches_ollama(monkeypatch, capsys):
    import helm_core.knowledge.semantic_extract as extract_module

    def explode(*args, **kwargs):
        raise AssertionError("fingerprint subcommand must not call Ollama")

    monkeypatch.setattr(extract_module, "_call_ollama", explode)
    monkeypatch.setattr("sys.argv", [
        "semantic_benchmark", "fingerprint", "--model", "gemma2:2b",
        "--git-sha", "abc123", "--model-digest", "8ccf136fdd52", "--keep-alive", "0",
    ])
    main()
    fp = json.loads(capsys.readouterr().out)
    assert fp["model_tag"] == "gemma2:2b"
    assert "fingerprint_hash" in fp


def _perfect_extraction(case):
    from helm_core.knowledge.semantic_extract import ExtractedAtom, ExtractedEdge, ExtractedEntity
    return WindowExtraction(
        entities=[ExtractedEntity(local_id=e.ref, entity_type=e.entity_type, label=e.label,
                                  subtype=e.subtype, aliases=e.aliases) for e in case.entities],
        atoms=[ExtractedAtom(local_id=a.ref, kind=a.kind, title=a.canonical_text[:40],
                             text=a.canonical_text, subtype=a.subtype, occurred_at=a.occurred_at,
                             date_precision=a.date_precision) for a in case.atoms],
        edges=[ExtractedEdge(from_local_id=e.from_ref, relation_type=e.relation_type,
                             to_local_id=e.to_ref, role=e.role) for e in case.edges],
    )


def _complete_result_json(tmp_path, *, model="gemma2:2b"):
    def fake_extract(text, *, domain, heading_path=(), model, keep_alive=None):
        case = next(c for c in GOLDEN_CASES if c.text == text)
        return _perfect_extraction(case)

    report = run_golden_benchmark(model=model, extract_fn=fake_extract, stability_repeats=1)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(golden_report_to_dict(report), ensure_ascii=False))
    return path


def _run_validate(monkeypatch, capsys, *args):
    monkeypatch.setattr("sys.argv", ["semantic_benchmark", "validate", *args])
    try:
        main()
        code = 0
    except SystemExit as exc:
        code = exc.code
    return code, capsys.readouterr().out


def test_validate_accepts_a_complete_correct_result(tmp_path, monkeypatch, capsys):
    path = _complete_result_json(tmp_path)
    code, out = _run_validate(monkeypatch, capsys, "--file", str(path), "--expect-model", "gemma2:2b")
    assert code == 0
    assert "VALID" in out


def test_validate_rejects_wrong_model(tmp_path, monkeypatch, capsys):
    path = _complete_result_json(tmp_path, model="gemma2:2b")
    code, out = _run_validate(monkeypatch, capsys, "--file", str(path), "--expect-model", "qwen2.5:3b")
    assert code == 1
    assert "INVALID" in out and "model" in out


def test_validate_rejects_missing_case_id(tmp_path, monkeypatch, capsys):
    path = _complete_result_json(tmp_path)
    data = json.loads(path.read_text())
    data["runs"] = [r for r in data["runs"] if r["case_id"] != "no_knowledge"]
    data["schema_stats"]["cases_total"] = len(data["runs"])
    path.write_text(json.dumps(data))
    code, out = _run_validate(monkeypatch, capsys, "--file", str(path), "--expect-model", "gemma2:2b")
    assert code == 1
    assert "no_knowledge" in out


def test_validate_rejects_duplicate_case_id(tmp_path, monkeypatch, capsys):
    path = _complete_result_json(tmp_path)
    data = json.loads(path.read_text())
    data["runs"].append(data["runs"][0])
    path.write_text(json.dumps(data))
    code, out = _run_validate(monkeypatch, capsys, "--file", str(path), "--expect-model", "gemma2:2b")
    assert code == 1
    assert "повтор" in out.lower()


def test_validate_rejects_wrong_cases_total(tmp_path, monkeypatch, capsys):
    path = _complete_result_json(tmp_path)
    data = json.loads(path.read_text())
    data["schema_stats"]["cases_total"] = 999
    path.write_text(json.dumps(data))
    code, out = _run_validate(monkeypatch, capsys, "--file", str(path), "--expect-model", "gemma2:2b")
    assert code == 1
    assert "cases_total" in out


def test_validate_rejects_malformed_json(tmp_path, monkeypatch, capsys):
    path = tmp_path / "broken.json"
    path.write_text("{not valid json")
    code, out = _run_validate(monkeypatch, capsys, "--file", str(path), "--expect-model", "gemma2:2b")
    assert code == 1
    assert "INVALID" in out


def test_validate_rejects_fingerprint_mismatch_when_expected_given(tmp_path, monkeypatch, capsys):
    path = _complete_result_json(tmp_path)
    data = json.loads(path.read_text())
    data["fingerprint"] = {"fingerprint_hash": "aaa"}
    path.write_text(json.dumps(data))
    code, out = _run_validate(monkeypatch, capsys, "--file", str(path), "--expect-model", "gemma2:2b",
                              "--expect-fingerprint-hash", "bbb")
    assert code == 1
    assert "fingerprint" in out.lower()


def test_validate_accepts_matching_fingerprint(tmp_path, monkeypatch, capsys):
    path = _complete_result_json(tmp_path)
    data = json.loads(path.read_text())
    data["fingerprint"] = {"fingerprint_hash": "matching-hash"}
    path.write_text(json.dumps(data))
    code, out = _run_validate(monkeypatch, capsys, "--file", str(path), "--expect-model", "gemma2:2b",
                              "--expect-fingerprint-hash", "matching-hash")
    assert code == 0
    assert "VALID" in out
