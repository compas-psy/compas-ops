"""Бесплатный parser router (ТЗ §14.6): MarkItDown fast path → Docling
quality path. Оба локальные/open-source, без платного API.

Импорты markitdown/docling — ВНУТРИ функций, не на уровне модуля.
`helm-core` (живой FastAPI-контейнер, лимит памяти 768MB, обслуживает
реальные вебхуки MAX/Telegram) никогда не импортирует и не запускает
парсеры — это делает только отдельный воркер (`worker.py`,
`Dockerfile.worker`), опрашивающий `knowledge_ingest_jobs`. Причина
разделения: Docling тянет ~5.7GB зависимостей (torch, OCR-модели) и при
разборе скана/сложного PDF может дать заметный скачок RAM — тяжёлый
файл не должен иметь возможность уронить процесс, отвечающий на живые
сообщения владельца (см. `implementation-state/STATUS.json`, решение
по архитектуре P8.5.2 от 29.08.2026).

Пороги качества калиброваны эмпирически на реальных синтетических
фикстурах (не «на глаз») — см. комментарии у констант.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from pathlib import Path

from .audio import is_audio_file, transcribe_audio
from .tables import restore_table_rows

#: Пустой/почти пустой результат — явный провал извлечения, не «короткий
#: документ». 20 символов — заведомо меньше любого осмысленного факта.
MIN_TEXT_LENGTH = 20

#: Символ замены Unicode (U+FFFD) — прямой признак ошибки декодирования.
MAX_REPLACEMENT_CHAR_RATIO = 0.05

#: НАЙДЕНО живым тестом (не гипотеза): PDF, где текст нарисован шрифтом
#: без нужных глифов (например Helvetica без кириллицы), извлекается не
#: как U+FFFD, а как валидный, но полностью испорченный текст — реальный
#: случай дал "HELM Knowledge: pdf fixture test.\nnnnnnnn: nnnnnnnnnn
#: Postgres." (кириллица схлопнулась в повторяющееся 'n'). Замер
#: доминирующей буквы на разборе НАСТОЯЩИХ документов (docx/pptx/xlsx/
#: чистый pdf) дал 0.105–0.154; на сломанном PDF — 0.346. Порог 0.25 —
#: чистый разрыв между этими двумя группами, не догадка.
MAX_DOMINANT_CHAR_RATIO = 0.25


@dataclass
class ParseResult:
    text: str
    parser: str  # "markitdown" | "docling" | "gigaam"
    quality_ok: bool


def _dominant_char_ratio(text: str) -> float:
    letters = [c.lower() for c in text if c.isalpha()]
    if not letters:
        return 0.0
    counts = collections.Counter(letters)
    _, top_count = counts.most_common(1)[0]
    return top_count / len(letters)


def _quality_ok(text: str) -> bool:
    """§14.6 parser quality gate: непустой текст, без abnormal replacement
    characters, без признаков испорченного шрифта/кодировки."""
    stripped = text.strip()
    if len(stripped) < MIN_TEXT_LENGTH:
        return False
    if text.count("�") / max(len(text), 1) > MAX_REPLACEMENT_CHAR_RATIO:
        return False
    if _dominant_char_ratio(text) > MAX_DOMINANT_CHAR_RATIO:
        return False
    return True


def _parse_with_markitdown(path: Path) -> str:
    from markitdown import MarkItDown

    result = MarkItDown().convert(str(path))
    return result.text_content


def _parse_with_docling(path: Path) -> str:
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(str(path))
    return result.document.export_to_markdown()


#: fb2 — XML, и конвертера для него у MarkItDown нет: файл распознаётся
#: как text/xml и возвращается PlainTextConverter'ом как есть. ИЗМЕРЕНО
#: 07.09.2026 на книге владельца: разметка проходила `_quality_ok()`
#: (валидный текст, ни одного U+FFFD, буквы распределены нормально), а
#: `rechunk()` не находил в XML ни одной пустой строки — вся книга
#: становилась ОДНИМ чанком, и бот честно отвечал «сохранено
#: фрагментов: 1».
FB2_SUFFIX = ".fb2"

#: Обёртки fb2, внутри которых лежат те же `<p>`: разворачиваются, а не
#: схлопываются в один абзац.
_FB2_CONTAINERS = frozenset({"epigraph", "cite", "poem", "stanza", "annotation"})

#: Части имени автора и их порядок. Именно перечислением, а не «все
#: дети `<author>`»: там же лежат `<email>`, `<home-page>` и `<id>`,
#: которым в тексте книги делать нечего.
_FB2_NAME_TAGS = ("first-name", "middle-name", "last-name", "nickname")


def _fb2_text(elem) -> str:
    """Текст элемента без внутренней разметки: `<p>Первый <emphasis>абзац
    </emphasis>.</p>` → «Первый абзац.». Куски склеиваются встык, а не
    через пробел: пробелы уже стоят в исходнике, лишний развалил бы
    пунктуацию."""
    return " ".join("".join(elem.itertext()).split())


def _fb2_blocks(elem, depth: int, out: list[str]) -> None:
    for child in elem:
        tag = child.tag.rpartition("}")[2]
        if tag == "section":
            _fb2_blocks(child, depth + 1, out)
        elif tag == "title":
            # Заголовок из нескольких `<p>` — отдельные блоки, а не одна
            # строка: без разделителя «Часть первая» и «Начало» слиплись
            # бы в «Часть перваяНачало».
            parts = [_fb2_text(line) for line in child]
            text = " ".join(part for part in parts if part) or _fb2_text(child)
            if text:
                out.append("#" * max(1, min(depth, 6)) + " " + text)
        elif tag in _FB2_CONTAINERS:
            _fb2_blocks(child, depth, out)
        else:
            text = _fb2_text(child)
            if text:
                out.append(text)


def _parse_fb2(path: Path) -> str:
    """Разделы и абзацы книги вместо её разметки.

    Заголовок раздела становится Markdown-заголовком по глубине
    вложенности — ровно та форма, которую `chunking._is_heading()` уже
    умеет читать: заголовок приклеивается к тексту под ним и обрывает
    склейку на границе раздела. Абзацы разделяются пустой строкой,
    потому что это единственная граница, которую видит `rechunk()`.
    """
    # defusedxml, а не stdlib: разбор идёт над присланным файлом, а
    # ElementTree разворачивает сущности и на «billion laughs» кладёт
    # воркер. Ставить нечего — это объявленная зависимость markitdown,
    # то есть пакет уже стоит везде, где вообще работают парсеры.
    from defusedxml.ElementTree import parse

    root = parse(str(path)).getroot()
    out: list[str] = []
    # Глубина ТЕЛА, не раздела: заголовок раздела берёт глубину своего
    # раздела, а `_fb2_blocks()` увеличивает её, спускаясь внутрь. С
    # названием книги главы идут `##` под ним, без названия — `#`.
    depth = 0
    title = root.find("./{*}description/{*}title-info/{*}book-title")
    if title is not None and _fb2_text(title):
        out.append("# " + _fb2_text(title))
        depth = 1
    author = root.find("./{*}description/{*}title-info/{*}author")
    if author is not None:
        parts = [_fb2_text(part) for tag in _FB2_NAME_TAGS
                 for part in author.findall("{*}" + tag)]
        name = " ".join(part for part in parts if part)
        if name:
            out.append("Автор: " + name)
    for body in root.findall("./{*}body"):
        _fb2_blocks(body, depth, out)
    return "\n\n".join(out)


def parse_file(path: Path) -> ParseResult:
    """Fast path (MarkItDown) сначала; при провале quality gate —
    эскалация на Docling (quality path). Если Docling тоже не проходит
    gate — вызывающий код обязан выставить `status=NEEDS_REVIEW`, не
    создавать уверенные knowledge facts (§14.6 — «bad fast-path
    extraction escalates to Docling», «если Docling тоже FAIL — source
    status NEEDS_REVIEW»).

    Аудио/видео (§14.7, ADR-021) — отдельная ветка ДО MarkItDown/Docling:
    ни один из них не умеет речь, попытка "распарсить" .ogg как документ
    заведомо провалила бы quality gate. Тот же `_quality_ok()` gate
    применяется и к транскрипту — пустая/бессмысленная расшифровка
    эскалирует в NEEDS_REVIEW тем же путём, что и плохой документ.
    
    fb2 — тоже отдельная ветка ДО MarkItDown, и по обратной причине: он
    её не отвергает, а принимает как обычный текст (см. `FB2_SUFFIX`).
    """
    if is_audio_file(path):
        text = transcribe_audio(path)
        return ParseResult(text=text, parser="gigaam", quality_ok=_quality_ok(text))

    if path.suffix.lower() == FB2_SUFFIX:
        text = _parse_fb2(path)
        return ParseResult(text=text, parser="fb2", quality_ok=_quality_ok(text))

    # ТАБЛИЦЫ ВОССТАНАВЛИВАЮТСЯ ЗДЕСЬ, А НЕ ПРИ ПРОВЕРКЕ ОТВЕТА.
    #
    # Разбор владельца 07.09.2026: «Проверка на выходе не восстановит
    # отношения, потерянные при извлечении PDF». Извлекатель
    # разворачивает лабораторный бланк по колонкам — название, значение,
    # единица и диапазон становятся четырьмя отдельными строками, а
    # чанкинг режет между ними. Дальше в чанке лежит число без имени, и
    # никакая проверка ответа его владельцу уже не вернёт.
    text = restore_table_rows(_parse_with_markitdown(path))
    if _quality_ok(text):
        return ParseResult(text=text, parser="markitdown", quality_ok=True)

    text = restore_table_rows(_parse_with_docling(path))
    return ParseResult(text=text, parser="docling", quality_ok=_quality_ok(text))
