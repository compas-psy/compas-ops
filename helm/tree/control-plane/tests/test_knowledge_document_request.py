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


def test_выбор_из_нескольких_документов_доводится_до_выдачи(session):
    """Уточняющий вопрос обязан уметь услышать ответ на себя.

    ДЕФЕКТ (скриншот владельца 10.09.2026). На «отдай исходный файл
    последнего клинического анализа крови» бот перечислил пять
    документов и спросил «Какой из них?». Владелец назвал нужный — и
    получил ПЕРЕСКАЗ содержимого: следующая реплика не содержит слов
    «отдай файл», `detect_document_request` её не узнаёт, и она уходит
    обычным путём вопроса к памяти.

    То есть бот задавал вопрос, ответ на который заведомо не мог
    обработать. Тупик по построению, а не редкий сбой.
    """
    bind_knowledge_user(session, None)
    ingest_text(session, domain="health", text="Гемоглобин 148 г/л",
                original_filename="анализ-крови-2023.pdf")
    ingest_text(session, domain="health", text="Гемоглобин 151 г/л",
                original_filename="анализ-крови-2024.pdf")
    session.flush()

    asked = probe(session, query="отдай исходный файл анализа крови")
    assert "Какой из них?" in (asked.answer_text or "")
    assert asked.sources, (
        "перечисленные документы обязаны дойти до следующего хода: плагин "
        "строит context.source_ids именно из sources прошлого ответа")

    context = DialogueContext(
        question="отдай исходный файл анализа крови",
        source_ids=tuple(s["source_id"] for s in asked.sources),
        filenames=tuple(s["original_filename"] for s in asked.sources),
        memory=True)
    chosen = probe(session, query="анализ-крови-2024.pdf", context=context)

    assert "анализ-крови-2024.pdf" in (chosen.answer_text or "")
    assert "ключом доступа" in (chosen.answer_text or ""), (
        "выбранный документ выдаётся так же, как единственный (§14.15)")
    assert "анализ-крови-2023.pdf" not in (chosen.answer_text or "")


def test_выбор_узнаётся_и_по_части_названия(session):
    """Владелец называет документ словами, а не именем файла.

    В его диалоге это было «Исследование гликированного гемоглобина —
    последний», а файл называется «148990953_Исследования гликированного
    гемоглобина.pdf». Требовать точного имени значило бы починить только
    тот случай, которого не бывает.
    """
    bind_knowledge_user(session, None)
    ingest_text(session, domain="health", text="HbA1c 5.4 %",
                original_filename="148990953_Исследования гликированного гемоглобина.pdf")
    ingest_text(session, domain="health", text="Гемоглобин 148 г/л",
                original_filename="гемоглобин-крови-2023.pdf")
    session.flush()

    asked = probe(session, query="отдай исходный файл гемоглобин")
    assert "Какой из них?" in (asked.answer_text or "")
    context = DialogueContext(
        question="отдай исходный файл гемоглобин",
        source_ids=tuple(s["source_id"] for s in asked.sources),
        filenames=tuple(s["original_filename"] for s in asked.sources),
        memory=True)

    chosen = probe(session, query="Исследование гликированного гемоглобина - последний",
                   context=context)

    assert "гликированного гемоглобина" in (chosen.answer_text or "")
    assert "ключом доступа" in (chosen.answer_text or "")


def test_новый_вопрос_после_уточнения_не_считается_выбором(session):
    """Не всякая реплика после «Какой из них?» — выбор документа.

    Если следующий вопрос не называет ни одного из перечисленных, он
    обязан остаться вопросом к памяти. Иначе починка тупика превратилась
    бы в захват разговора.
    """
    bind_knowledge_user(session, None)
    ingest_text(session, domain="health", text="Гемоглобин 148 г/л",
                original_filename="анализ-крови-2023.pdf")
    ingest_text(session, domain="health", text="Гемоглобин 151 г/л",
                original_filename="анализ-крови-2024.pdf")
    session.flush()

    asked = probe(session, query="отдай исходный файл анализа крови")
    context = DialogueContext(
        question="отдай исходный файл анализа крови",
        source_ids=tuple(s["source_id"] for s in asked.sources),
        filenames=tuple(s["original_filename"] for s in asked.sources),
        memory=True)

    other = probe(session, query="а какой у меня был гемоглобин", context=context)

    assert "ключом доступа" not in (other.answer_text or "")


def _context_of(*sources) -> DialogueContext:
    """Контекст ровно той формы, что шлёт плагин после «Какой из них?».

    Строится из источников напрямую, а не из первого хода: проверяется
    ВЫБОР, и он не должен зависеть от того, как сработал поиск кандидатов.
    Пропуск теста здесь был бы худшим исходом — он выглядит как
    прохождение, ничего не проверив.
    """
    return DialogueContext(
        question="Отдай мне файл последнего клинического анализа крови",
        source_ids=tuple(str(source.id) for source in sources),
        filenames=tuple(source.original_filename or "" for source in sources),
        memory=True)

def test_вопрос_о_содержании_не_считается_выбором_на_настоящих_именах(session):
    """Прогон 530 на живом сервере: выбор захватил обычный вопрос.

    Мой прежний тест этого не поймал, потому что файлы в нём назывались
    «анализ-крови-2023.pdf» — выдуманно удобно, слова «гемоглобин» в имени
    не было. У владельца файл называется «148990953_Исследования
    гликированного гемоглобина.pdf», и вопрос «а какой у меня был
    гемоглобин» совпал с ним одним словом — документ выдался вместо
    ответа.

    Здесь имена настоящие, из его корпуса. Проверка не может пройти
    из-за удачной выдумки.
    """
    bind_knowledge_user(session, None)
    hba1c = ingest_text(
        session, domain="health", text="HbA1c 5.4 %",
        original_filename="148990953_Исследования гликированного гемоглобина.pdf")
    blood = ingest_text(
        session, domain="health", text="Гемоглобин 148 г/л",
        original_filename="94574021_Клинический анализ крови.pdf")
    session.flush()

    context = _context_of(hba1c, blood)

    answer = probe(session, query="а какой у меня был гемоглобин", context=context)

    assert "ключом доступа" not in (answer.answer_text or ""), (
        "вопрос о содержании обязан остаться вопросом, даже когда слово из "
        "него есть в имени документа")


def test_название_документа_словами_по_прежнему_доводит_до_выдачи(session):
    """Та же правка не должна убить то, ради чего всё делалось."""
    bind_knowledge_user(session, None)
    hba1c = ingest_text(
        session, domain="health", text="HbA1c 5.4 %",
        original_filename="148990953_Исследования гликированного гемоглобина.pdf")
    blood = ingest_text(
        session, domain="health", text="Гемоглобин 148 г/л",
        original_filename="94574021_Клинический анализ крови.pdf")
    session.flush()

    context = _context_of(hba1c, blood)

    for reply in ("Вот этот: 148990953_Исследования гликированного гемоглобина.pdf",
                  "Исследование гликированного гемоглобина - последний"):
        answer = probe(session, query=reply, context=context)
        assert "гликированного гемоглобина" in (answer.answer_text or ""), reply
        assert "ключом доступа" in (answer.answer_text or ""), reply


def test_названный_документ_выдаётся_даже_если_его_не_было_в_списке(session):
    """Прогон 534: список стал точнее — и сломал соседнее.

    После правки подбора под «файл последнего клинического анализа крови»
    предлагаются только анализы крови. Владелец называет документ про
    гликированный гемоглобин — его в списке нет, и бот ответил
    содержимым вместо файла. Но просьба была о файле, и документ назван
    точно: искать надо по всему корпусу, а не только среди предложенного.
    """
    bind_knowledge_user(session, None)
    blood = ingest_text(session, domain="health", text="Гемоглобин 167 г/л",
                        original_filename="94574021_Клинический анализ крови.pdf")
    ingest_text(
        session, domain="health", text="HbA1c 5.4 %",
        original_filename="148990953_Исследования гликированного гемоглобина.pdf")
    session.flush()

    # В контексте — ТОЛЬКО анализ крови: ровно то, что предложил бы бот.
    context = _context_of(blood)

    answer = probe(session,
                   query="Вот этот: 148990953_Исследования гликированного гемоглобина.pdf",
                   context=context)

    assert "гликированного гемоглобина" in (answer.answer_text or "")
    assert "ключом доступа" in (answer.answer_text or ""), (
        "документ назван точно — его надо отдать, а не пересказать")
