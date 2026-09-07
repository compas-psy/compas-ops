"""Единица поиска: не строка бланка, а блок с заголовком над ним.

Корень плохих ответов, измеренный 05.09.2026 (прогон 307, разбор в
`docs/CHUNKING_AND_BAD_ANSWERS_2026-09-05.md`): `split_chunks()` режет
текст по пустой строке, а в Markdown из PDF пустая строка стоит после
каждого заголовка, каждой строки бланка и каждого лабораторного
значения. Бланк превращается в набор отдельных «документов».

Замер на 953 чанках 90 источников: медиана 65 символов, 250 чанков
короче 20 символов, 390 из 953 не предложения. «Дата: 24.08.2026 09:58»
— отдельный чанк, оторванный от события. «Врач: Безручко Дарья Юрьевна
______» — тоже, и пять таких одинаковых строк заняли весь колчан из
пяти доказательств, не оставив места ни вектору, ни ответу.

ПРАВИЛО. Три вещи, каждая против измеренного дефекта:

  заголовок не бывает чанком сам по себе — он приклеивается к тексту
  под ним, потому что «ОСМОТР ЭНДОКРИНОЛОГА» без осмотра не ответ;

  короткие блоки склеиваются подряд, пока не наберут минимум, потому
  что строка бланка в одиночку не единица смысла;

  заголовок склейку обрывает — блоки из разных разделов в один чанк не
  попадают, иначе поиск начнёт отвечать соседним разделом.

ЧЕТВЁРТОЕ, добавлено 07.09.2026 по книге владельца: блок длиннее
максимума режется по границам строк. Пустая строка — единственная
граница, которую видит этот файл, и документ без пустых строк (fb2 до
своего парсера, длинная Markdown-таблица) целиком становился ОДНИМ
чанком: не единица поиска, а весь документ, который ранжировать не по
чему. У Postgres на этом стоит и жёсткий предел — `to_tsvector` на
тексте больше 1 048 575 байт лексем падает с ошибкой, то есть
загрузка проваливалась бы вся целиком.

ЧЕГО ЗДЕСЬ НЕТ НАМЕРЕННО. Разбиения строки: строка бланка или строка
таблицы делится только вместе, и одна строка длиннее максимума так и
остаётся одним чанком — заголовки таблицы к её строкам это всё равно
не приклеит, а это отдельная задача с отдельным замером. Никакой чистки
текста: чанк обязан совпадать с источником посимвольно, иначе цитата
перестанет быть цитатой.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import KnowledgeChunk
from .embeddings import embed_texts_or_none
from .health_schema import health_schema_configured, is_health_domain, write_chunks

#: Ниже этого склеиваем со следующим блоком. 200 символов — примерно
#: абзац; выбрано по замеру: при медиане 65 всё, что короче, оказалось
#: строкой бланка, а не мыслью.
MIN_CHUNK_CHARS = 200

#: Выше этого не склеиваем дальше даже при недоборе следующего.
MAX_CHUNK_CHARS = 1200

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")

#: Заголовок Markdown («## Осмотр») или строка-заголовок бланка целиком
#: заглавными («ОСМОТР ЭНДОКРИНОЛОГА»). Вторая форма — из замера: PDF
#: превращается в Markdown без решёток, и заголовок отличим только
#: регистром.
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_CAPS_HEADING_MAX = 80


def _is_heading(block: str) -> bool:
    if _MD_HEADING_RE.match(block):
        return True
    if "\n" in block or len(block) > _CAPS_HEADING_MAX:
        return False
    letters = [ch for ch in block if ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters)


def _split_long(block: str) -> list[str]:
    """Блок длиннее максимума — несколько чанков по границам строк.

    Граница только между строками: строка бланка («Врач: … ______») и
    строка таблицы — неделимые единицы, и разрез внутри них оставил бы
    значение без названия. Поэтому одна строка длиннее максимума
    возвращается как есть — это известный предел, а не недосмотр.
    """
    if len(block) <= MAX_CHUNK_CHARS:
        return [block]
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for line in block.split("\n"):
        if current and size + len(line) > MAX_CHUNK_CHARS:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts


def rechunk(text: str) -> list[str]:
    """Блоки текста как единицы поиска: заголовок сверху, короткое слито.

    Возвращает список чанков в порядке текста. Пустой текст даёт пустой
    список — вызывающий сам решает, что с этим делать; раньше
    `split_chunks()` в этом случае возвращал список с пустой строкой, и
    это порождало чанк ни о чём.
    """
    blocks = [part for b in _PARAGRAPH_SPLIT.split(text) if b.strip()
              for part in _split_long(b.strip())]
    chunks: list[str] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        body = "\n\n".join(buffer)
        chunks.append(f"{heading}\n{body}" if heading else body)
        buffer = []

    for block in blocks:
        if _is_heading(block):
            # Заголовок обрывает склейку: то, что было до него,
            # относится к прежнему разделу.
            flush()
            heading = block
            continue
        buffer.append(block)
        if sum(len(b) for b in buffer) + 2 * (len(buffer) - 1) >= MIN_CHUNK_CHARS:
            flush()
    flush()

    # Заголовок в самом конце файла без текста под ним потерялся бы
    # молча. Он редко несёт ответ, но потеря должна быть решением, а не
    # побочным эффектом: возвращаем его отдельным чанком.
    if heading and not any(chunk.startswith(heading) for chunk in chunks):
        chunks.append(heading)
    return chunks


def store_chunks(session: Session, *, source_id: uuid.UUID,
                 knowledge_user_id: uuid.UUID, domain: str, text: str) -> int:
    """Нарезать текст источника и положить чанки на место прежних.

    ОДНА функция на три вызова — загрузку текстом, загрузку файлом и
    пересборку поискового слоя. Третий путь появился 06.09.2026, и
    писать его отдельной копией значило бы гарантировать расхождение:
    пересобранные чанки обязаны быть теми же, что сделал бы ingest.

    Удаление прежних чанков перед вставкой — то, что делает пересборку
    возможной, и одновременно то, что делает повтор задания безопасным.
    На первой загрузке удалять нечего. Тот же приём и та же причина, что
    у `relations.py::store_relations()`.

    ЭМБЕДДИНГ СЧИТАЕТСЯ ЗДЕСЬ ЖЕ, не отдельным проходом. Чанк и его
    вектор — одна единица поиска: заменить текст и оставить прежний
    вектор значит получить векторный поиск, отвечающий по тому, чего в
    базе больше нет. Ровно эта авария случилась 06.09.2026 на другом
    слое, когда переключение поколения semantic-v3 оставило слой
    личностей над прежним.

    Возвращает число записанных чанков.
    """
    chunks = rechunk(text)
    # ADR-025: недоступность embed-сервиса не мешает создать чанки —
    # `embed_texts_or_none()` отдаёт None на чанк, лексический поиск по
    # нему работает как раньше.
    embeddings = embed_texts_or_none(chunks)

    if is_health_domain(domain) and health_schema_configured():
        # ADR-005/P12: текст чанка — самое чувствительное поле источника,
        # уходит в health.knowledge_chunks своей ролью и своей сессией.
        return write_chunks(source_id=source_id, knowledge_user_id=knowledge_user_id,
                            chunks=chunks, embeddings=embeddings)

    session.query(KnowledgeChunk).filter(
        KnowledgeChunk.knowledge_user_id == knowledge_user_id,
        KnowledgeChunk.source_id == source_id,
    ).delete(synchronize_session=False)
    for ordinal, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
        session.add(KnowledgeChunk(
            knowledge_user_id=knowledge_user_id, source_id=source_id, ordinal=ordinal,
            text=chunk_text,
            # to_tsvector на стороне БД, не в Python: русская конфигурация
            # словаря живёт в Postgres, и дублировать её здесь значит
            # разойтись с ней при первом же обновлении.
            tsv=func.to_tsvector("russian", chunk_text),
            embedding=embedding,
        ))
    return len(chunks)
