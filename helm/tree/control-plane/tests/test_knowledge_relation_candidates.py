"""R4.6.C2 (владелец 03.09.2026) — deterministic candidate generator.

Проверяется НЕ LLM (здесь его нет вовсе) — чистая функция близости
текста. Каждый критерий (A-D) показан отдельно: должен сработать на
своём кейсе и НЕ сработать там, где условие не выполнено — иначе
слишком широкий критерий тихо вернул бы «связать всё со всем», ровно
то, от чего уходит R4.6.C2 после R4.6.C."""

from __future__ import annotations

from helm_core.knowledge.relation_candidates import (
    ADJACENT_SENTENCE_PROXIMITY_CHARS, generate_candidates,
)
from helm_core.knowledge.semantic_extract import ExtractedAtom, ExtractedEntity


def _entity(local_id: str, label: str, evidence_quote: str, aliases: tuple[str, ...] = ()) -> ExtractedEntity:
    return ExtractedEntity(local_id=local_id, entity_type="PERSON", label=label,
                           aliases=aliases, evidence_quote=evidence_quote)


def test_overlapping_spans_are_a_candidate():
    """A: атом, чья evidence — весь текст, пересекается с сущностью
    внутри него."""
    text = "19 августа встретились с Ивановым Петром по важному делу."
    atom = ExtractedAtom(local_id="a1", kind="event", title="Встреча", text="...", evidence_quote=text)
    entity = _entity("e1", "Иванов Пётр", "Ивановым Петром")

    cands = generate_candidates([entity], [atom], text)

    assert len(cands) == 1
    assert cands[0].reason == "overlap"
    assert {cands[0].from_id, cands[0].to_id} == {"a1", "e1"}


def test_alias_mentioned_inside_the_other_objects_evidence_is_a_candidate():
    """B: alias одной сущности встречается дословно внутри evidence
    другого объекта, даже если сами spans не пересекаются."""
    text = "Кузнецов К.И. подписал документ. Кузнецов Кирилл Игоревич — директор филиала."
    e1 = _entity("e1", "Кузнецов К.И.", "Кузнецов К.И. подписал документ.")
    e2 = _entity("e2", "Кузнецов Кирилл Игоревич", "Кузнецов Кирилл Игоревич — директор филиала.",
                 aliases=("Кузнецов К.И.",))

    cands = generate_candidates([e1, e2], [], text)

    assert any(c.reason == "mention" for c in cands)


def test_same_sentence_is_a_candidate():
    """C (предложение): два объекта в одном предложении — кандидат,
    даже если их evidence-цитаты не пересекаются и не упоминают друг
    друга дословно."""
    text = "Кузнецов Игорь и Волкова Елена вместе организовали встречу."
    e1 = _entity("e1", "Кузнецов Игорь", "Кузнецов Игорь")
    e2 = _entity("e2", "Волкова Елена", "Волкова Елена")

    cands = generate_candidates([e1, e2], [], text)

    assert len(cands) == 1 and cands[0].reason == "same_sentence"


def test_same_paragraph_is_a_candidate_regardless_of_distance():
    """C (абзац): владелец не поставил лимит расстояния для «один
    абзац» — только для правила D (соседние предложения через границу).
    Три предложения между объектами внутри ОДНОГО абзаца — всё ещё
    кандидат."""
    filler = " " + "X" * 250 + "."
    text = f"Кузнецов Игорь подготовил отчёт.{filler} Волкова Елена его утвердила."
    e1 = _entity("e1", "Кузнецов Игорь", "Кузнецов Игорь подготовил отчёт.")
    e2 = _entity("e2", "Волкова Елена", "Волкова Елена его утвердила.")

    cands = generate_candidates([e1, e2], [], text)

    assert len(cands) == 1 and cands[0].reason == "same_paragraph"


def test_adjacent_sentences_within_proximity_window_is_a_candidate():
    """D: соседние предложения, разрыв внутри
    ADJACENT_SENTENCE_PROXIMITY_CHARS — кандидат."""
    text = "Кузнецов Игорь подготовил отчёт. Волкова Елена его утвердила."
    e1 = _entity("e1", "Кузнецов Игорь", "Кузнецов Игорь подготовил отчёт.")
    e2 = _entity("e2", "Волкова Елена", "Волкова Елена его утвердила.")

    cands = generate_candidates([e1, e2], [], text)

    assert len(cands) == 1
    assert cands[0].reason in ("same_paragraph", "adjacent_sentence")


def test_different_paragraphs_far_apart_with_no_mention_is_not_a_candidate():
    """Отрицательный кейс — ядро мандата R4.6.C2: НЕ каждая пара
    объектов в одном окне становится кандидатом. Разные абзацы, большое
    расстояние, никто никого не упоминает — пара НЕ проходит ни один
    из критериев A-D."""
    filler = " Текст-заполнитель." * 30
    text = f"Кузнецов Игорь работает в отделе продаж.{filler}\n\nВолкова Елена работает в отделе кадров."
    e1 = _entity("e1", "Кузнецов Игорь", "Кузнецов Игорь работает в отделе продаж.")
    e2 = _entity("e2", "Волкова Елена", "Волкова Елена работает в отделе кадров.")

    cands = generate_candidates([e1, e2], [], text)

    assert cands == []


def test_from_id_is_the_earlier_appearing_object_deterministically():
    """Владелец: генератор сам решает from/to (не классификатор) —
    детерминированно, по позиции в тексте, не по порядку в списке
    аргументов."""
    text = "Волкова Елена и Кузнецов Игорь вместе организовали встречу."
    # Порядок в списке — намеренно "неправильный" (e2 первый), чтобы
    # доказать: from/to определяются положением в ТЕКСТЕ, не порядком
    # передачи аргументов.
    e_kuznetsov = _entity("e_kuznetsov", "Кузнецов Игорь", "Кузнецов Игорь")
    e_volkova = _entity("e_volkova", "Волкова Елена", "Волкова Елена")

    cands = generate_candidates([e_kuznetsov, e_volkova], [], text)

    assert len(cands) == 1
    assert cands[0].from_id == "e_volkova"  # раньше в тексте
    assert cands[0].to_id == "e_kuznetsov"


def test_evidence_context_does_not_leak_the_whole_window():
    """Владелец п.5: classifier должен искать evidence связи ВНУТРИ
    контекста кандидата, не во всём окне — контекст обязан быть УЖЕ
    полного текста, когда в окне есть посторонний материал."""
    filler_before = "Ничего не значащее вступление. " * 5
    filler_after = " Ничего не значащее заключение." * 5
    text = filler_before + "Кузнецов Игорь и Волкова Елена вместе организовали встречу." + filler_after
    e1 = _entity("e1", "Кузнецов Игорь", "Кузнецов Игорь")
    e2 = _entity("e2", "Волкова Елена", "Волкова Елена")

    cands = generate_candidates([e1, e2], [], text)

    assert len(cands) == 1
    assert len(cands[0].evidence_context) < len(text)
    assert "Кузнецов Игорь" in cands[0].evidence_context
    assert "Волкова Елена" in cands[0].evidence_context


def test_unlocatable_evidence_is_skipped_not_crashed():
    """Объект, чья evidence_quote почему-то не находится в окне (не
    должно происходить после R4.5.3 grounding, но генератор не имеет
    права упасть на этом) — просто не участвует ни в одном кандидате."""
    text = "Кузнецов Игорь работает в отделе продаж."
    e1 = _entity("e1", "Кузнецов Игорь", "Кузнецов Игорь работает в отделе продаж.")
    e2 = _entity("e2", "Призрак", "Этой фразы нет в тексте вообще")

    cands = generate_candidates([e1, e2], [], text)

    assert cands == []


def test_proximity_constant_is_positive_and_bounded():
    """Не сам расчёт, а инвариант: константа должна быть положительной
    и разумно маленькой (владелец: «ограниченная» proximity window) —
    защита от случайного 0 или огромного числа при будущей правке."""
    assert 0 < ADJACENT_SENTENCE_PROXIMITY_CHARS <= 1000
