"""Контракт ответа владельца от 05.09.2026 и маршрутизация к нему.

Разбор production (docs/PRODUCTION_ANSWERS_RCA_2026-09-05.md) показал
две вещи, которые проверяются здесь и нигде больше:

1. Живой вопрос не проходил через query-router вообще — распознавания
   намерения в `probe()` не было. Теперь есть, и тест ловит именно
   маршрут: структурный вопрос обязан уйти в доказанное и НЕ дойти до
   поиска по чанкам.
2. Все десять бесплатных ответов за всё время были списком ровно из
   пяти сырых фрагментов. Контракт запрещает такой вывод прямо.

База не нужна: маршрут — это решение по тексту вопроса, а форматтер
чистая функция.
"""

from __future__ import annotations

import uuid

import pytest

from helm_core.knowledge import probe as probe_mod
from helm_core.knowledge.answer_format import NOT_FOUND, format_doctors, format_nearest_quote
from helm_core.knowledge.query_router import (
    AnswerPath, DoctorItem, DoctorsAnswer, Proof, QuestionIntent,
)

TENANT = uuid.UUID("00000000-0000-0000-0000-00000000beef")

#: Слова, которых в ответе владельцу быть не должно, пока он о них не
#: спросил. Список дословно из распоряжения.
BANNED = ("документ", "чанк", "фрагмент", "уверенност", "граф", "evidence",
          "возможно", "вероятно", "похоже", "совпадений")


def _answer(*items: DoctorItem) -> DoctorsAnswer:
    a = DoctorsAnswer(question="каких врачей я посещал?",
                      intent=QuestionIntent.DOCTORS_VISITED)
    a.items = list(items)
    a.path_used = AnswerPath.EVIDENCE if items else AnswerPath.NONE
    return a


def _doctor(person, specialties=(), dates=()) -> DoctorItem:
    return DoctorItem(identity_id=str(uuid.uuid4()), person=person,
                      specialties=list(specialties), dates=list(dates),
                      proofs=[Proof(source_id="s")])


# ── форматтер: порядок и содержание ──────────────────────────────────

def test_specialties_come_first_and_names_after():
    text = format_doctors(_answer(
        _doctor("Иванов И. И.", ["гастроэнтеролог"], ["2026-03-12"]),
        _doctor("Петрова А. С.", ["эндокринолог"]),
    ))
    first = text.splitlines()[0]
    assert first == "Гастроэнтеролог, эндокринолог."
    # ФИО есть, но вторым планом — не в первой строке.
    assert "Иванов" not in first
    assert "Иванов И. И. — гастроэнтеролог — 2026-03-12" in text


def test_doctor_with_proven_role_but_no_specialty_is_not_lost():
    """Дефект из распоряжения: доказанный врач без специальности не
    должен исчезнуть из ответа и не должен считаться непокрытым."""
    answer = _answer(
        _doctor("Иванов И. И.", ["гастроэнтеролог"]),
        _doctor("Сидоров П. П."),
    )
    text = format_doctors(answer)
    assert "Ещё 1 врач без подтверждённой специальности." in text
    assert "Сидоров П. П." in text
    assert answer.uncovered_identities == 0


def test_only_unproven_specialties_still_answers():
    text = format_doctors(_answer(_doctor("Иванов И. И."), _doctor("Петров П. П.")))
    assert text.startswith("Есть 2 врача без подтверждённой специальности.")


def test_no_items_says_so_in_one_line():
    assert format_doctors(_answer()) == NOT_FOUND
    assert "\n" not in NOT_FOUND


def test_many_doctors_are_counted_not_listed():
    text = format_doctors(_answer(*[_doctor(f"Врач {i}", ["терапевт"]) for i in range(6)]))
    assert "Всего 6 врачей." in text
    assert "Врач 0" not in text


@pytest.mark.parametrize("answer", [
    _answer(_doctor("Иванов И. И.", ["гастроэнтеролог"], ["2026-03-12"])),
    _answer(_doctor("Иванов И. И."), _doctor("Петров П. П.", ["уролог"])),
    _answer(),
])
def test_answer_never_contains_banned_words(answer):
    text = format_doctors(answer).lower()
    for word in BANNED:
        assert word not in text, word


def test_answer_is_at_most_four_short_sentences():
    text = format_doctors(_answer(
        _doctor("Иванов И. И.", ["гастроэнтеролог"], ["2026-03-12"]),
        _doctor("Сидоров П. П."),
    ))
    assert len(text.splitlines()) <= 4


@pytest.mark.parametrize("count,word", [
    (1, "врач"), (2, "врача"), (4, "врача"), (5, "врачей"),
    (11, "врачей"), (21, "врач"), (22, "врача"), (25, "врачей"),
])
def test_plural_forms(count, word):
    from helm_core.knowledge.answer_format import _plural_doctors
    assert _plural_doctors(count) == word


# ── композер: список из пяти кусков больше не выдаётся ────────────────

def test_multiple_chunks_no_longer_enumerated():
    evidence = [
        probe_mod.Evidence(chunk_id=str(i), source_id="s", chunk_text=f"кусок {i}",
                           original_filename="выписка.pdf", rank=1.0 - i / 10)
        for i in range(5)
    ]
    text, mode = probe_mod._compose_answer(evidence)
    assert mode == "Z1"
    assert "Найдено" not in text
    assert "кусок 0" in text
    for i in range(1, 5):
        assert f"кусок {i}" not in text


def test_single_chunk_answer_unchanged():
    evidence = [probe_mod.Evidence(chunk_id="1", source_id="s", chunk_text="давление 120/80",
                                   original_filename="выписка.pdf", rank=1.0)]
    text, mode = probe_mod._compose_answer(evidence)
    assert mode == "Z0"
    assert text == "давление 120/80\n\nИсточник: выписка.pdf"


def test_nearest_quote_names_itself_nearest():
    text = format_nearest_quote("давление 120/80", "выписка.pdf")
    assert text.startswith("Не нашёл прямого ответа.")
    assert "давление 120/80" in text
    assert "Источник: выписка.pdf" in text


# ── маршрут: структурный вопрос не доходит до поиска по чанкам ────────

class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def _no_chunk_search(monkeypatch):
    def boom(*a, **kw):  # pragma: no cover - вызов означает провал теста
        raise AssertionError("структурный вопрос ушёл в поиск по чанкам")
    monkeypatch.setattr(probe_mod, "_lexical_search", boom)
    monkeypatch.setattr(probe_mod, "_health_lexical_search", boom)
    monkeypatch.setattr(probe_mod, "embed_texts_or_none", boom)


def _stub_common(monkeypatch):
    monkeypatch.setattr(probe_mod, "bind_knowledge_user", lambda s, u: TENANT)
    monkeypatch.setattr(probe_mod, "is_future_reminder", lambda q: False)
    monkeypatch.setattr(probe_mod, "search_memories", lambda *a, **kw: [])
    _no_chunk_search(monkeypatch)


def test_doctors_question_goes_to_proven_path(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(
        probe_mod, "answer_doctors_visited",
        lambda session, *, question, knowledge_user_id: _answer(
            _doctor("Иванов И. И.", ["гастроэнтеролог"])))

    session = _FakeSession()
    result = probe_mod.probe(session, query="каких врачей я посещал в этом году?")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.mode == "S1"
    assert result.answer_text.splitlines()[0] == "Гастроэнтеролог."
    assert len(session.added) == 1
    assert session.added[0].mode == "S1"
    assert session.added[0].paid_ai_used is False
    assert session.added[0].evidence_count == 1


def test_recognised_question_without_proof_is_not_escalated(monkeypatch):
    """Пусто по доказанному — честный отказ, а не платная модель: она
    истории владельца не знает и заполнит пустоту рассуждениями."""
    _stub_common(monkeypatch)
    monkeypatch.setattr(
        probe_mod, "answer_doctors_visited",
        lambda session, *, question, knowledge_user_id: _answer())

    result = probe_mod.probe(_FakeSession(), query="каких врачей я посещал?")

    assert result.outcome == "LOCAL_ANSWER"
    assert result.answer_text == NOT_FOUND


def test_unrecognised_question_still_goes_the_old_way(monkeypatch):
    """Маршрут добавлен, а не подменён: вопрос без известного намерения
    обязан по-прежнему идти в поиск по чанкам."""
    monkeypatch.setattr(probe_mod, "bind_knowledge_user", lambda s, u: TENANT)
    monkeypatch.setattr(probe_mod, "is_future_reminder", lambda q: False)
    monkeypatch.setattr(probe_mod, "search_memories", lambda *a, **kw: [])
    monkeypatch.setattr(probe_mod, "health_schema_configured", lambda: False)
    monkeypatch.setattr(probe_mod, "embed_texts_or_none", lambda t: [None])
    seen = []

    def fake_lexical(session, *, query, domain, knowledge_user_id):
        seen.append(query)
        return []

    monkeypatch.setattr(probe_mod, "_lexical_search", fake_lexical)
    monkeypatch.setattr(
        probe_mod, "answer_doctors_visited",
        lambda *a, **kw: pytest.fail("невопрос о врачах ушёл в структурный путь"))

    result = probe_mod.probe(_FakeSession(), query="что было в анализе 12 марта")

    assert seen == ["что было в анализе 12 марта"]
    assert result.outcome == "NEEDS_REASONING"


# ── распознавание намерения: цена ошибки выросла ──────────────────────

@pytest.mark.parametrize("question", [
    "каких врачей я посещал?",
    "Каких врачей я посещал в этом году?",
    "к каким врачам я ходил",
    "какие врачи меня наблюдали",
])
def test_enumeration_questions_are_recognised(question):
    from helm_core.knowledge.query_router import detect_intent
    assert detect_intent(question) == QuestionIntent.DOCTORS_VISITED


@pytest.mark.parametrize("question", [
    # Содержит и «врач», и «приём»: по двум условиям ушло бы в перечень
    # специальностей — уверенный ответ не на тот вопрос.
    "что сказал врач на приёме",
    "когда я был у врача последний раз",
    "что сказал врач про давление",
    "где я был в марте",
])
def test_non_enumeration_questions_are_not_hijacked(question):
    from helm_core.knowledge.query_router import detect_intent
    assert detect_intent(question) == QuestionIntent.UNSUPPORTED


# ── год из вопроса ────────────────────────────────────────────────────

def test_year_is_read_from_question():
    from datetime import date

    from helm_core.knowledge.query_router import requested_year
    assert requested_year("Каких врачей я посещал в 2014 году?") == 2014
    assert requested_year("Каких врачей я посещал в этом году?",
                          today=date(2026, 9, 5)) == 2026
    assert requested_year("Каких врачей я посещал?") is None
    # «за прошлый год» намеренно не разбирается: угадать значит ответить
    # не на тот вопрос.
    assert requested_year("Каких врачей я посещал за прошлый год?") is None


def test_undated_doctor_is_not_attributed_to_a_year():
    from helm_core.knowledge.query_router import _split_by_year
    dated = _doctor("Иванов И. И.", ["гастроэнтеролог"], ["2026-03-12"])
    other = _doctor("Петров П. П.", ["уролог"], ["2019-05-01"])
    undated = _doctor("Сидоров П. П.", ["эндокринолог"])

    matched, no_date, other_year = _split_by_year([dated, other, undated], 2026)

    assert [i.person for i in matched] == ["Иванов И. И."]
    assert no_date == 1
    assert other_year == 1


def test_year_without_proof_says_the_year_out_loud():
    answer = _answer()
    answer.year = 2014
    assert format_doctors(answer) == (
        "Не нашёл в ваших данных подтверждённых посещений врачей за 2014 год.")


def test_year_without_proof_does_not_hide_that_doctors_exist():
    """Промолчать нельзя: «не нашёл за 2014» прочиталось бы как «врачей в
    данных нет», а они есть — просто привязать их к году нечем."""
    answer = _answer()
    answer.year = 2014
    answer.undated_doctors = 3
    text = format_doctors(answer)
    assert text.splitlines()[0].endswith("за 2014 год.")
    assert "В данных есть 3 врача, дату приёма у которых подтвердить не удалось." in text
    # Ни одного имени: врачей не называем, раз к году их отнести нечем.
    assert "Иванов" not in text


def test_year_answer_mentions_undated_doctors_alongside_the_list():
    answer = _answer(_doctor("Иванов И. И.", ["гастроэнтеролог"], ["2026-03-12"]))
    answer.year = 2026
    answer.undated_doctors = 2
    text = format_doctors(answer)
    assert text.splitlines()[0] == "Гастроэнтеролог."
    assert "Ещё 2 врача без подтверждённой даты приёма" in text
