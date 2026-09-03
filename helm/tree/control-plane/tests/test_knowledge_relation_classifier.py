"""R4.6.C2 (владелец 03.09.2026) — relation-or-NONE classifier.

Не проверяется качество классификации (то же ограничение, что и у
остальных semantic_extract*-тестов — качество меряет R4-бенчмарк).
Проверяется контракт: NONE — валидный явный исход, не ошибка; evidence
обязан быть заземлён в КОНТЕКСТЕ КАНДИДАТА, не во всём окне; неизвестный
тип сводится к related_to тем же реестром, что и single/two-pass."""

from __future__ import annotations

import json

import helm_core.knowledge.semantic_extract as semantic_extract
from helm_core.knowledge.relation_candidates import RelationCandidate
from helm_core.knowledge.relation_classifier import C2_MAX_ATTEMPTS, classify_relation
from helm_core.knowledge.semantic_extract import ExtractedAtom, ExtractedEdge, ExtractedEntity

CANDIDATE = RelationCandidate(from_id="a1", to_id="e1",
                              evidence_context="Приём вёл терапевт Иванов.", reason="same_sentence")
FROM_OBJ = ExtractedAtom(local_id="a1", kind="event", title="Приём", text="...",
                         evidence_quote="Приём вёл терапевт Иванов.")
TO_OBJ = ExtractedEntity(local_id="e1", entity_type="PERSON", label="Иванов", evidence_quote="Иванов")


def _fake_ollama(response: dict):
    def fake(prompt, *, model, keep_alive=None,
            system=semantic_extract.SYSTEM_PROMPT, response_schema=semantic_extract.RESPONSE_SCHEMA):
        return json.dumps(response)
    return fake


def test_explicit_none_is_a_valid_outcome_not_an_error(monkeypatch):
    monkeypatch.setattr(semantic_extract, "_call_ollama", _fake_ollama({"relation": "NONE"}))

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None and reason is None


def test_valid_typed_relation_grounded_in_candidate_context(monkeypatch):
    monkeypatch.setattr(semantic_extract, "_call_ollama", _fake_ollama(
        {"relation": "involves", "evidence_quote": "Приём вёл терапевт Иванов."}))

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge == ExtractedEdge(from_local_id="a1", relation_type="involves", to_local_id="e1",
                                 evidence_quote="Приём вёл терапевт Иванов.")
    assert reason is None


def test_evidence_outside_candidate_context_is_rejected_even_if_plausible(monkeypatch):
    """Владелец п.5 — ядро усиленного grounding: evidence должен быть
    внутри КОНТЕКСТА КАНДИДАТА, а не где угодно в окне. Цитата ниже
    правдоподобна как русский текст, но её нет в `CANDIDATE.evidence_
    context` — этого достаточно для отказа, окно целиком тут ни при чём."""
    monkeypatch.setattr(semantic_extract, "_call_ollama", _fake_ollama(
        {"relation": "involves", "evidence_quote": "Совсем другая фраза из другого места."}))

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None
    assert reason is not None and "не найден в контексте кандидата" in reason


def test_relation_without_evidence_quote_is_rejected():
    """`relation` без `evidence_quote` — недостаточное доказательство,
    трактуется как отказ, не как акт доверия модели на слово."""
    import helm_core.knowledge.relation_classifier as rc
    original = semantic_extract._call_ollama
    semantic_extract._call_ollama = _fake_ollama({"relation": "involves"})
    try:
        edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                         model="test", keep_alive="0")
    finally:
        semantic_extract._call_ollama = original
    assert edge is None and reason is not None


def test_unknown_relation_type_normalizes_to_related_to(monkeypatch):
    """Тот же реестр §14.9, что у single/two-pass — не отдельная копия
    правила (R4.6.B нашёл именно эту нормализацию как источник
    систематической потери типа)."""
    monkeypatch.setattr(semantic_extract, "_call_ollama", _fake_ollama(
        {"relation": "performed_by", "evidence_quote": "Приём вёл терапевт Иванов."}))

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge.relation_type == "related_to"


def test_malformed_json_retries_bounded_then_degrades_to_none_not_a_crash(monkeypatch):
    """Одна плохая пара не имеет права обрушить всё окно (в отличие от
    single/two-pass, где malformed JSON — provал всего вызова): при
    исчерпании попыток classify_relation возвращает управляемый отказ,
    а не бросает исключение наружу."""
    calls = []

    def broken(prompt, *, model, keep_alive=None,
              system=semantic_extract.SYSTEM_PROMPT, response_schema=semantic_extract.RESPONSE_SCHEMA):
        calls.append(prompt)
        return "не json"

    monkeypatch.setattr(semantic_extract, "_call_ollama", broken)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None
    assert reason is not None and "попыток" in reason
    assert len(calls) == C2_MAX_ATTEMPTS


def test_prompt_names_only_the_two_candidate_objects(monkeypatch):
    """Ядро мандата: классификатор не видит третьих объектов — они
    физически отсутствуют в промпте, значит модель не может на них
    сослаться."""
    captured = []

    def fake(prompt, *, model, keep_alive=None,
            system=semantic_extract.SYSTEM_PROMPT, response_schema=semantic_extract.RESPONSE_SCHEMA):
        captured.append(prompt)
        return json.dumps({"relation": "NONE"})

    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)
    classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ, model="test", keep_alive="0")

    assert "a1" in captured[0] and "e1" in captured[0]
    assert "Иванов" in captured[0]
