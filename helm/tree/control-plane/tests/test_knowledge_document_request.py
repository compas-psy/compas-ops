"""§14.15 в боте: «отдай сам файл» — просьба о документе, а не вопрос.

ДЕФЕКТ (скриншоты владельца 07.09.2026). На «отдай сам pdf последнего
клинического анализа крови» бот отвечал пересказом, а на уточнение
«Отдай сам файл, а не текст, как он назывался» — «не нашёл». Выдача
оригинала существовала только в веб-панели: `documents.py` импортировал
один `api/panel.py`, и у бота пути к файлу не было вовсе.
"""

import pytest

from helm_core.knowledge.documents import detect_document_request
from helm_core.knowledge.ingest import ingest_text
from helm_core.knowledge.probe import probe
from helm_core.knowledge.query_spec import DialogueContext
from helm_core.knowledge.tenancy import bind_knowledge_user


@pytest.mark.parametrize("text, subject", [
    ("Отдай сам pdf последнего клинического анализа крови",
     "последнего клинического анализа крови"),
    ("отдай оригинал договора с подрядчиком", "договора с подрядчиком"),
    ("пришли файл выписки", "выписки"),
    # Уточнение после ответа: просьба есть, документ не назван.
    ("Отдай сам файл, а не текст, как он назывался", ""),
])
def test_a_request_for_the_file_itself_is_recognised(text, subject):
    assert detect_document_request(text) == subject


@pytest.mark.parametrize("text", [
    "что сказано в документе о сроках",
    "дай ссылку на мой канал",
    "какой у меня был холестерин в последний раз",
    "сколько документов я загрузил",
])
def test_a_question_about_content_is_not_a_request_for_the_file(text):
    """Вопрос о содержании обязан остаться вопросом.

    Без глагола передачи «в документе сказано» попало бы сюда, и ответом
    на вопрос стала бы ссылка на файл.
    """
    assert detect_document_request(text) is None


def test_the_bot_answers_with_the_document_not_a_retelling(session):
    bind_knowledge_user(session, None)
    ingest_text(session, domain="health", text="Гемоглобин 148 г/л, лейкоциты 5.4",
                original_filename="анализ-крови-2023.pdf")
    session.flush()

    result = probe(session, query="отдай сам pdf анализ крови")

    assert result.outcome == "LOCAL_ANSWER"
    assert "анализ-крови-2023.pdf" in result.answer_text
    assert "ключом доступа" in result.answer_text, (
        "выдача оригинала обязана вести туда, где спросят passkey (§14.15)")


def test_a_request_without_a_name_uses_the_document_from_the_previous_turn(session):
    """Ровно случай владельца: ответ по анализу, следом «отдай сам файл»."""
    bind_knowledge_user(session, None)
    source = ingest_text(session, domain="health", text="Гемоглобин 148 г/л",
                         original_filename="анализ-крови-2023.pdf")
    session.flush()

    result = probe(session, query="Отдай сам файл, а не текст, как он назывался",
                   context=DialogueContext(question="какой у меня гемоглобин",
                                           source_ids=(str(source.id),),
                                           filenames=("анализ-крови-2023.pdf",),
                                           memory=True))

    assert result.outcome == "LOCAL_ANSWER"
    assert "анализ-крови-2023.pdf" in result.answer_text


def test_several_matching_documents_get_a_short_clarification(session):
    bind_knowledge_user(session, None)
    for year in (2023, 2024):
        ingest_text(session, domain="health", text=f"Анализ крови за {year} год",
                    original_filename=f"анализ-крови-{year}.pdf")
    session.flush()

    result = probe(session, query="отдай оригинал анализ-крови")

    assert result.outcome == "LOCAL_ANSWER"
    assert "несколько" in result.answer_text.lower()
    assert "анализ-крови-2023.pdf" in result.answer_text
    assert "анализ-крови-2024.pdf" in result.answer_text


def test_nothing_matching_is_an_honest_ask_not_a_retelling(session):
    bind_knowledge_user(session, None)
    ingest_text(session, domain="personal", text="Заметка про поездку в Крым",
                original_filename="поездка.md")
    session.flush()

    result = probe(session, query="отдай оригинал договора аренды")

    assert result.outcome == "LOCAL_ANSWER"
    assert "Назовите документ" in result.answer_text
