"""QuerySpec: что именно спросили, до того как это исполнять.

Распоряжение владельца 06.09.2026: «Реализуй общий QuerySpec/executor с
проверкой доступа, времени, источников и полноты результата. Не
создавай отдельный обработчик под каждый пример».

До этого свойства вопроса выяснялись по дороге в разных местах: тенант в
начале, год внутри исполнителя врачей, область данных перед решением об
оплате, неприменимый период в форматтере. Ни одно не существовало как
факт, о котором можно спросить.

Базы не нужно: разбор идёт по форме вопроса.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from helm_core.knowledge.query_spec import (INTENT_DECISION, INTENT_ENUMERATE,
                                            INTENT_LOOKUP, build_query_spec)

TENANT = uuid.UUID("00000000-0000-0000-0000-00000000beef")
TODAY = datetime.date(2026, 9, 6)


def spec(question: str, **kw):
    return build_query_spec(question, tenant_id=TENANT, today=TODAY, **kw)


# ── доступ ───────────────────────────────────────────────────────────

def test_tenant_is_part_of_the_query_not_a_default():
    """v3.8 §14.4: «every query starts with knowledge_user_id». Тенант
    входит в запрос, а не подставляется исполнителем по умолчанию."""
    assert spec("каких врачей я посещал?").tenant_id == TENANT


# ── намерение ────────────────────────────────────────────────────────

@pytest.mark.parametrize("question,intent", [
    ("каких врачей я посещал?", INTENT_ENUMERATE),
    ("перечисли места работы", INTENT_ENUMERATE),
    ("какое у меня было давление?", INTENT_LOOKUP),
    ("где я работал в 2019 году?", INTENT_LOOKUP),
    ("что решили по подзадачам в ТЗ?", INTENT_DECISION),
])
def test_intent_says_what_counts_as_an_answer(question, intent):
    assert spec(question).intent == intent


def test_decision_wins_over_enumeration():
    """«Какие решения приняли» — и перечисление, и решение. Список
    решений без самих решений ответом не будет."""
    assert spec("какие решения приняли по этапам?").intent == INTENT_DECISION


# ── время ────────────────────────────────────────────────────────────

def test_applicable_and_inapplicable_time_are_different_fields():
    """«Периода нет» и «период есть, но применить нечем» — разные вещи,
    и до 06.09.2026 второе молча превращалось в первое."""
    both = spec("каких врачей я посещал в марте 2025?").time
    assert both.year == 2025
    assert both.unsupported == "в марте"

    none = spec("каких врачей я посещал?").time
    assert none.year is None and none.unsupported is None
    assert none.present is False


def test_this_year_resolves_by_the_calendar_not_by_the_corpus():
    assert spec("каких врачей я посещал в этом году?").time.year == 2026


# ── область данных ───────────────────────────────────────────────────

def test_question_about_own_record_is_personal_without_possessives():
    """«Что решили по подзадачам в ТЗ» — ни «мой», ни «я», а платная
    модель этого ТЗ всё равно не знает. Различает связка «спрашивают о
    факте» и «речь о записи»."""
    assert spec("что решили по подзадачам в ТЗ?").personal is True


@pytest.mark.parametrize("question", [
    "что такое ферритин?",
    "переведи текст",
    "сколько будет два плюс два",
    "какая погода завтра",
])
def test_general_question_stays_general(question):
    assert spec(question).personal is False


# ── уточнение ────────────────────────────────────────────────────────

def test_deictic_without_antecedent_asks_instead_of_guessing():
    result = spec("что там прописал врач?")
    assert result.clarification is not None
    assert "уточните" in result.clarification.lower()


def test_dialogue_context_removes_the_need_to_ask():
    """Правило про указательное слово общее, а не про врачей. Появится
    память диалога — менять придётся вызов, а не правило."""
    assert spec("что там прописал врач?", has_dialogue_context=True).clarification is None


def test_a_question_that_names_its_subject_needs_no_clarification():
    assert spec("что прописал кардиолог в заключении от 12.03.2025?").clarification is None
