"""R4.6.C — two-pass extraction эксперимент.

Не проверяется качество извлечения (то же ограничение, что и у
`test_knowledge_semantic_extract.py` — качество меряет R4-бенчмарк на
golden-наборе). Проверяется структурный контракт: pass 2 не может
создать факт, которого не было в pass 1, и переиспользует ТУ ЖЕ
grounding/реестр-логику, что и single-pass, а не собственную копию.
"""

import json

import pytest

import helm_core.knowledge.semantic_extract as semantic_extract
from helm_core.knowledge.semantic_extract_twopass import extract_window_two_pass

WINDOW_TEXT = (
    "19 августа 2026 года в клинике был приём у уролога Кириченко "
    "Сергея Александровича. Приём вёл Кириченко."
)


def pass1_payload(**overrides) -> str:
    data = {
        "entities": [{"local_id": "e1", "entity_type": "PERSON",
                      "label": "Кириченко Сергей Александрович", "aliases": [],
                      "evidence_quote": "Кириченко Сергея Александровича"}],
        "atoms": [{"local_id": "a1", "kind": "EVENT", "title": "Приём уролога",
                   "text": "Приём вёл Кириченко.", "occurred_at": "2026-08-19",
                   "date_precision": "DAY", "evidence_quote": WINDOW_TEXT}],
        # pass 1 всё равно пытается предложить edges (это тот же
        # extract_window(), что и у single-pass) — они должны быть
        # ПОЛНОСТЬЮ отброшены итоговым результатом, не смешаны с pass 2.
        "edges": [{"from": "a1", "type": "CONTRADICTS", "to": "e1",
                   "evidence_quote": "Приём вёл Кириченко."}],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def _install_fake_ollama(monkeypatch, *, pass1: str, pass2: str) -> list[bool]:
    """Возвращает список: True — вызов пришёл с pass1-схемой/промптом,
    False — с pass2. Используется, чтобы проверить, что pass 2 вообще
    вызывается со СВОИМ system/schema, а не с production-константами."""
    seen_pass1 = []

    def fake_call_ollama(prompt, *, model, keep_alive=None,
                         system=semantic_extract.SYSTEM_PROMPT,
                         response_schema=semantic_extract.RESPONSE_SCHEMA):
        is_pass1 = system is semantic_extract.SYSTEM_PROMPT
        seen_pass1.append(is_pass1)
        return pass1 if is_pass1 else pass2

    monkeypatch.setattr(semantic_extract, "_call_ollama", fake_call_ollama)
    return seen_pass1


def test_pass2_relation_replaces_pass1_relation_entirely(monkeypatch) -> None:
    """pass 1 предложил `contradicts`, но право решать связи — у pass 2:
    итоговый edge должен быть ТОЛЬКО тем, что вернул pass 2, а не смесью."""
    pass2 = json.dumps({"edges": [
        {"from": "a1", "type": "involves", "to": "e1", "evidence_quote": "Приём вёл Кириченко."}]})
    _install_fake_ollama(monkeypatch, pass1=pass1_payload(), pass2=pass2)

    result = extract_window_two_pass(WINDOW_TEXT, domain="health", model="test-model")

    assert [(e.from_local_id, e.relation_type, e.to_local_id) for e in result.edges] == [
        ("a1", "involves", "e1")]
    assert [e.local_id for e in result.entities] == ["e1"]
    assert [a.local_id for a in result.atoms] == ["a1"]


def test_pass2_cannot_invent_a_new_local_id(monkeypatch) -> None:
    """Ядро мандата владельца: pass 2 «не имеет права создавать новые
    факты/entities» — не обещание в промпте, а структурный запрет через
    тот же `known`-набор, что использует `validate()` в single-pass."""
    pass2 = json.dumps({"edges": [
        {"from": "a1", "type": "involves", "to": "e99", "evidence_quote": "Приём вёл Кириченко."}]})
    _install_fake_ollama(monkeypatch, pass1=pass1_payload(), pass2=pass2)

    result = extract_window_two_pass(WINDOW_TEXT, domain="health", model="test-model")

    assert result.edges == []
    assert any("никуда" in r for r in result.rejected)


def test_pass2_receives_pass1_objects_by_local_id(monkeypatch) -> None:
    """pass 2 должен видеть local_id/evidence pass 1, иначе он не сможет
    сослаться на них корректно."""
    captured_prompts = []

    def fake_call_ollama(prompt, *, model, keep_alive=None,
                         system=semantic_extract.SYSTEM_PROMPT,
                         response_schema=semantic_extract.RESPONSE_SCHEMA):
        if system is semantic_extract.SYSTEM_PROMPT:
            return pass1_payload()
        captured_prompts.append(prompt)
        return json.dumps({"edges": []})

    monkeypatch.setattr(semantic_extract, "_call_ollama", fake_call_ollama)
    extract_window_two_pass(WINDOW_TEXT, domain="health", model="test-model")

    assert len(captured_prompts) == 1
    assert "e1" in captured_prompts[0] and "a1" in captured_prompts[0]
    assert "Кириченко Сергей Александрович" in captured_prompts[0]


def test_unknown_relation_type_still_normalizes_to_related_to(monkeypatch) -> None:
    """pass 2 переиспользует `_validate_edges()` — тот же реестр §14.9,
    те же правила, не отдельная (и потенциально разъезжающаяся) копия."""
    pass2 = json.dumps({"edges": [
        {"from": "a1", "type": "выдуманный_тип", "to": "e1", "evidence_quote": "Приём вёл Кириченко."}]})
    _install_fake_ollama(monkeypatch, pass1=pass1_payload(), pass2=pass2)

    result = extract_window_two_pass(WINDOW_TEXT, domain="health", model="test-model")

    assert [e.relation_type for e in result.edges] == ["related_to"]
    assert any("related_to" in r for r in result.rejected)


def test_edge_without_grounded_evidence_is_dropped(monkeypatch) -> None:
    """Тот же R4.5.3 grounding-контракт: evidence_quote связи обязан
    быть дословной подстрокой окна, иначе связь отбрасывается."""
    pass2 = json.dumps({"edges": [
        {"from": "a1", "type": "involves", "to": "e1", "evidence_quote": "этого текста в окне нет"}]})
    _install_fake_ollama(monkeypatch, pass1=pass1_payload(), pass2=pass2)

    result = extract_window_two_pass(WINDOW_TEXT, domain="health", model="test-model")

    assert result.edges == []
    assert any("не найден в тексте окна" in r for r in result.rejected)


def test_no_pass1_objects_skips_pass2_entirely(monkeypatch) -> None:
    """Нечего связывать — pass 2 не должен даже вызываться (нет смысла
    жечь ещё один HTTP-запрос ради заведомо пустого ответа)."""
    calls = []

    def fake_call_ollama(prompt, *, model, keep_alive=None,
                         system=semantic_extract.SYSTEM_PROMPT,
                         response_schema=semantic_extract.RESPONSE_SCHEMA):
        calls.append(system is semantic_extract.SYSTEM_PROMPT)
        return pass1_payload(entities=[], atoms=[], edges=[])

    monkeypatch.setattr(semantic_extract, "_call_ollama", fake_call_ollama)
    result = extract_window_two_pass(WINDOW_TEXT, domain="health", model="test-model")

    assert result.entities == [] and result.atoms == [] and result.edges == []
    assert calls == [True]  # только pass 1, pass 2 не вызывался


def test_pass2_malformed_json_retries_then_fails(monkeypatch) -> None:
    """Тот же repair-контракт §14.4.3, что у single-pass: ограниченное
    число попыток, затем ExtractionFailed — не пустой результат."""
    seen = []

    def fake_call_ollama(prompt, *, model, keep_alive=None,
                         system=semantic_extract.SYSTEM_PROMPT,
                         response_schema=semantic_extract.RESPONSE_SCHEMA):
        if system is semantic_extract.SYSTEM_PROMPT:
            return pass1_payload()
        seen.append(prompt)
        return "не json"

    monkeypatch.setattr(semantic_extract, "_call_ollama", fake_call_ollama)
    with pytest.raises(semantic_extract.ExtractionFailed):
        extract_window_two_pass(WINDOW_TEXT, domain="health", model="test-model", attempts=3)

    assert len(seen) == 3
    assert "Прошлый ответ отклонён" not in seen[0]
    assert "Прошлый ответ отклонён" in seen[1]


def test_never_leaves_the_machine() -> None:
    """§14.4.3 «Ollama/local deterministic extraction only» — тот же
    инвариант, что и у single-pass (`test_extraction_never_leaves_the_
    machine`), проверенный на новом модуле: он не заводит собственный
    сетевой адрес, а полагается на `semantic_extract._call_ollama()`."""
    import ast
    import inspect

    import helm_core.knowledge.semantic_extract_twopass as module

    tree = ast.parse(inspect.getsource(module))
    urls = [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "://" in node.value]

    assert urls == [], f"pass 2 не должен знать сетевые адреса напрямую: {urls}"
