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
    """
    if is_audio_file(path):
        text = transcribe_audio(path)
        return ParseResult(text=text, parser="gigaam", quality_ok=_quality_ok(text))

    text = _parse_with_markitdown(path)
    if _quality_ok(text):
        return ParseResult(text=text, parser="markitdown", quality_ok=True)

    text = _parse_with_docling(path)
    return ParseResult(text=text, parser="docling", quality_ok=_quality_ok(text))
