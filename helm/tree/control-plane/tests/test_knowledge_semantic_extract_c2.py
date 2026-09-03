"""R4.6.C2 (владелец 03.09.2026) — сборка deterministic candidate
generation + relation-or-NONE classifier в один `extract_window_c2()`.

Проверяется склейка, не качество: pass 1 даёт entities/atoms (те же,
что и single/two-pass — тот же `extract_window()`), детерминированный
генератор решает, КАКИЕ пары вообще стоит спросить, классификатор
отвечает про каждую данную пару отдельно."""

from __future__ import annotations

import json

import helm_core.knowledge.semantic_extract as semantic_extract
from helm_core.knowledge.semantic_extract_c2 import extract_window_c2

WINDOW_TEXT = "Приём вёл терапевт Иванов. Иванов назначил анализы."


def _pass1_payload(**overrides) -> str:
    data = {
        "entities": [{"local_id": "e1", "entity_type": "PERSON", "label": "Иванов",
                     "aliases": [], "evidence_quote": "Иванов"}],
        "atoms": [{"local_id": "a1", "kind": "EVENT", "title": "Приём", "text": "Приём вёл терапевт Иванов.",
                  "evidence_quote": "Приём вёл терапевт Иванов."}],
        # pass 1 всё равно пытается предложить edges (тот же extract_
        # window(), что у single-pass) — они должны быть ПОЛНОСТЬЮ
        # отброшены: право решать связи здесь у candidate generator +
        # classifier, не у pass 1.
        "edges": [{"from": "a1", "type": "contradicts", "to": "e1", "evidence_quote": "Иванов"}],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_pass1_edges_are_discarded_entirely(monkeypatch):
    def fake(prompt, *, model, keep_alive=None,
            system=semantic_extract.SYSTEM_PROMPT, response_schema=semantic_extract.RESPONSE_SCHEMA):
        if system is semantic_extract.SYSTEM_PROMPT:
            return _pass1_payload()
        return json.dumps({"relation": "NONE"})

    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)
    result = extract_window_c2(WINDOW_TEXT, domain="health", model="test-model")

    assert result.edges == [], "pass 1 предложил contradicts — classifier сказал NONE, итог должен быть пуст"
    assert [e.local_id for e in result.entities] == ["e1"]
    assert [a.local_id for a in result.atoms] == ["a1"]


def test_classifier_relation_is_used_when_it_finds_one(monkeypatch):
    def fake(prompt, *, model, keep_alive=None,
            system=semantic_extract.SYSTEM_PROMPT, response_schema=semantic_extract.RESPONSE_SCHEMA):
        if system is semantic_extract.SYSTEM_PROMPT:
            return _pass1_payload()
        return json.dumps({"relation": "involves", "evidence_quote": "Приём вёл терапевт Иванов."})

    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)
    result = extract_window_c2(WINDOW_TEXT, domain="health", model="test-model")

    assert [(e.from_local_id, e.relation_type, e.to_local_id) for e in result.edges] == [
        ("a1", "involves", "e1")]


def test_no_candidates_means_classifier_is_never_called(monkeypatch):
    """Сущность и атом, между которыми нет НИКАКОЙ доказуемой близости
    (разные абзацы, большая дистанция, не упоминают друг друга) — ни
    одного кандидата, значит classify_relation не вызывается вовсе."""
    far_text = ("Приём вёл терапевт Иванов." + " Текст-заполнитель." * 30
               + "\n\nСовершенно не связанное предложение про Волкову.")
    classify_calls = []

    def fake(prompt, *, model, keep_alive=None,
            system=semantic_extract.SYSTEM_PROMPT, response_schema=semantic_extract.RESPONSE_SCHEMA):
        if system is semantic_extract.SYSTEM_PROMPT:
            return json.dumps({
                "entities": [
                    {"local_id": "e1", "entity_type": "PERSON", "label": "Иванов",
                     "evidence_quote": "Приём вёл терапевт Иванов."},
                    {"local_id": "e2", "entity_type": "PERSON", "label": "Волкова",
                     "evidence_quote": "Совершенно не связанное предложение про Волкову."},
                ],
                "atoms": [], "edges": [],
            })
        classify_calls.append(prompt)
        return json.dumps({"relation": "NONE"})

    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)
    result = extract_window_c2(far_text, domain="personal", model="test-model")

    assert classify_calls == []
    assert result.edges == []
    assert len(result.entities) == 2
