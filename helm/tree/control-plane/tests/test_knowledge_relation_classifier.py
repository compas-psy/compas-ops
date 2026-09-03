"""R4.6.E (владелец 03.09.2026) — relation existence/typing redesign.

Не проверяется качество классификации (то же ограничение, что и у
остальных semantic_extract*-тестов — качество меряет R4-бенчмарк).
Проверяется контракт: existence (2A) и typing+direction (2B) — два
независимых вызова; entailed=false — валидный явный исход, не ошибка;
evidence обязан быть заземлён в КОНТЕКСТЕ КАНДИДАТА; неизвестный тип
из 2B ОТКЛОНЯЕТСЯ, а не сводится к related_to (владелец: «REJECT/NONE,
НИКОГДА coercion»); направление — часть ответа модели (2B), не порядок
появления объектов в тексте."""

from __future__ import annotations

import json

import helm_core.knowledge.semantic_extract as semantic_extract
from helm_core.knowledge.relation_candidates import RelationCandidate
from helm_core.knowledge.relation_classifier import (
    EXISTENCE_SYSTEM_PROMPT, MAX_ATTEMPTS, TYPING_SYSTEM_PROMPT, classify_existence, classify_relation,
)
from helm_core.knowledge.semantic_extract import ExtractedAtom, ExtractedEdge, ExtractedEntity

CANDIDATE = RelationCandidate(from_id="a1", to_id="e1",
                              evidence_context="Приём вёл терапевт Иванов.", reason="same_sentence")
FROM_OBJ = ExtractedAtom(local_id="a1", kind="event", title="Приём", text="...",
                         evidence_quote="Приём вёл терапевт Иванов.")
TO_OBJ = ExtractedEntity(local_id="e1", entity_type="PERSON", label="Иванов", evidence_quote="Иванов")


def _sequenced_ollama(*responses: dict | str):
    """Возвращает по одному ответу на каждый вызов `_call_ollama`, по
    порядку — 2A первый, 2B (если есть) второй. Строка передаётся как
    есть (для проверки malformed JSON), словарь сериализуется."""
    calls = []
    remaining = list(responses)

    def fake(prompt, *, model, keep_alive=None,
            system=semantic_extract.SYSTEM_PROMPT, response_schema=semantic_extract.RESPONSE_SCHEMA):
        calls.append((system, prompt))
        response = remaining.pop(0)
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)

    return fake, calls


def test_explicit_entailed_false_is_a_valid_outcome_not_an_error(monkeypatch):
    fake, calls = _sequenced_ollama({"entailed": False})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None and reason is None
    assert len(calls) == 1  # 2B не вызывается, если 2A не подтвердил существование


def test_entailed_true_direction_a_to_b(monkeypatch):
    fake, calls = _sequenced_ollama(
        {"entailed": True, "evidence_quote": "Приём вёл терапевт Иванов."},
        {"relation_type": "involves", "direction": "a_to_b"})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge == ExtractedEdge(from_local_id="a1", relation_type="involves", to_local_id="e1",
                                 evidence_quote="Приём вёл терапевт Иванов.")
    assert reason is None
    assert len(calls) == 2
    assert calls[0][0] is EXISTENCE_SYSTEM_PROMPT
    assert calls[1][0] is TYPING_SYSTEM_PROMPT


def test_entailed_true_direction_b_to_a_swaps_from_and_to(monkeypatch):
    """Владелец п.2D: направление решает МОДЕЛЬ, не порядок появления в
    тексте — `candidate.from_id/to_id` (a1, порождённый как «раньше
    встретился») не обязан совпадать с семантическим направлением."""
    fake, _ = _sequenced_ollama(
        {"entailed": True, "evidence_quote": "Приём вёл терапевт Иванов."},
        {"relation_type": "involves", "direction": "b_to_a"})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge == ExtractedEdge(from_local_id="e1", relation_type="involves", to_local_id="a1",
                                 evidence_quote="Приём вёл терапевт Иванов.")
    assert reason is None


def test_evidence_outside_candidate_context_is_rejected_at_2a(monkeypatch):
    """Ядро усиленного grounding (владелец): evidence должен быть внутри
    КОНТЕКСТА КАНДИДАТА, а не где угодно в окне."""
    fake, calls = _sequenced_ollama(
        {"entailed": True, "evidence_quote": "Совсем другая фраза из другого места."})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None
    assert reason is not None and "не найден в контексте кандидата" in reason
    assert len(calls) == 1  # 2B не вызывается — grounding провалился на 2A


def test_entailed_true_without_evidence_quote_is_rejected(monkeypatch):
    fake, calls = _sequenced_ollama({"entailed": True})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None and reason is not None
    assert len(calls) == 1


def test_entailed_as_string_false_is_not_misread_as_true(monkeypatch):
    """`bool("false")` в Python истинно — если модель (в обход схемы)
    сериализует boolean строкой, наивная проверка тихо инвертировала бы
    default. Строка должна сравниваться по значению."""
    fake, calls = _sequenced_ollama({"entailed": "false"})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None and reason is None
    assert len(calls) == 1


def test_unknown_relation_type_from_2b_is_rejected_not_coerced(monkeypatch):
    """Владелец: «Любой неизвестный relation: REJECT/NONE, НИКОГДА не
    coercion -> related_to» — прямая противоположность прежнему C2
    (`test_unknown_relation_type_normalizes_to_related_to`, теперь
    неверный тест, удалён вместе со старым поведением)."""
    fake, _ = _sequenced_ollama(
        {"entailed": True, "evidence_quote": "Приём вёл терапевт Иванов."},
        {"relation_type": "performed_by", "direction": "a_to_b"})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None
    assert reason is not None and "вне реестра" in reason and "related_to" not in reason.split("—")[0]


def test_invalid_direction_from_2b_is_rejected(monkeypatch):
    fake, _ = _sequenced_ollama(
        {"entailed": True, "evidence_quote": "Приём вёл терапевт Иванов."},
        {"relation_type": "involves", "direction": "sideways"})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None
    assert reason is not None and "направление" in reason


def test_2a_malformed_json_retries_bounded_then_degrades_to_rejection(monkeypatch):
    fake, calls = _sequenced_ollama("не json", "не json")
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None
    assert reason is not None and "попыток" in reason
    assert len(calls) == MAX_ATTEMPTS


def test_2b_malformed_json_after_confirmed_existence_retries_then_degrades(monkeypatch):
    """Существование ПОДТВЕРЖДЕНО (2A), но типизация (2B) не разобралась
    — рассматривается как отказ у ЭТОЙ пары, не крах всего окна, но
    сообщение обязано отличать «связь есть, тип не найден» от «связи
    нет» (иначе диагностика R4.6.E теряет ключевое различие)."""
    fake, calls = _sequenced_ollama(
        {"entailed": True, "evidence_quote": "Приём вёл терапевт Иванов."},
        "не json", "не json")
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    edge, reason = classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ,
                                     model="test", keep_alive="0")

    assert edge is None
    assert reason is not None and "подтверждена" in reason
    assert len(calls) == 1 + MAX_ATTEMPTS


def test_2a_prompt_names_only_the_two_candidate_objects_and_no_relation_registry(monkeypatch):
    """Ядро мандата (существование): классификатор 2A не видит третьих
    объектов, и — устраняя forced-choice bias, владелец п.1 — не видит
    реестра типов связи вообще: ему физически нечего «выбрать хоть
    что-то из списка», кроме да/нет."""
    fake, calls = _sequenced_ollama({"entailed": False})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    classify_relation(CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ, model="test", keep_alive="0")

    prompt = calls[0][1]
    assert "a1" in prompt and "e1" in prompt and "Иванов" in prompt
    assert "involves" not in EXISTENCE_SYSTEM_PROMPT and "related_to" not in EXISTENCE_SYSTEM_PROMPT


def test_classify_existence_true_returns_entailed_and_quote_without_calling_2b(monkeypatch):
    """R4.6.E.4 (микро-calibration) вызывает `classify_existence()`
    НАПРЯМУЮ, в изоляции от типизации — owner: «Здесь НЕТ списка
    relation types вообще» в этом шаге."""
    fake, calls = _sequenced_ollama(
        {"entailed": True, "evidence_quote": "Приём вёл терапевт Иванов."})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    entailed, evidence_quote, reason = classify_existence(
        CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ, model="test", keep_alive="0")

    assert entailed is True
    assert evidence_quote == "Приём вёл терапевт Иванов."
    assert reason is None
    assert len(calls) == 1


def test_classify_existence_false_returns_no_quote_no_reason(monkeypatch):
    fake, _ = _sequenced_ollama({"entailed": False})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    entailed, evidence_quote, reason = classify_existence(
        CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ, model="test", keep_alive="0")

    assert (entailed, evidence_quote, reason) == (False, None, None)


def test_classify_existence_ungrounded_evidence_is_false_with_reason(monkeypatch):
    fake, _ = _sequenced_ollama(
        {"entailed": True, "evidence_quote": "Совсем другая фраза из другого места."})
    monkeypatch.setattr(semantic_extract, "_call_ollama", fake)

    entailed, evidence_quote, reason = classify_existence(
        CANDIDATE, from_obj=FROM_OBJ, to_obj=TO_OBJ, model="test", keep_alive="0")

    assert entailed is False and evidence_quote is None
    assert reason is not None and "не найден в контексте кандидата" in reason


def test_typing_schema_enum_covers_full_registry_and_nothing_else():
    from helm_core.knowledge.relation_classifier import TYPING_RESPONSE_SCHEMA

    assert set(TYPING_RESPONSE_SCHEMA["properties"]["relation_type"]["enum"]) == semantic_extract._RELATION_TYPES
    assert set(TYPING_RESPONSE_SCHEMA["properties"]["direction"]["enum"]) == {"a_to_b", "b_to_a"}
