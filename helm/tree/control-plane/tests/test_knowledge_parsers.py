"""helm_core/knowledge/parsers.py — parser router (§14.6).

Пороговые функции (`_quality_ok`, `_dominant_char_ratio`) тестируются
без зависимостей — чистые строковые функции. `parse_file()` — реальными
файлами через реально установленный MarkItDown (не мок): единственный
способ доказать, что фикстуры и роутер работают вместе, а не гадать по
сигнатуре библиотеки. Docling — тяжёлая (~5.7GB) и требует сетевого
доступа к huggingface.co для первой загрузки моделей (недоступен из
песочницы разработки — см. `docs/KNOWLEDGE_MODELS.md`), поэтому
Docling-путь тестами не покрыт здесь: доказывается на живом сервере
(есть полноценный интернет), не выдаётся за проверенное без этого.
"""

from pathlib import Path

import pytest

markitdown = pytest.importorskip("markitdown", reason="markitdown — опциональная зависимость воркера, не control-plane")

from helm_core.knowledge import parsers  # noqa: E402
from helm_core.knowledge.audio import is_audio_file  # noqa: E402
from helm_core.knowledge.chunking import rechunk  # noqa: E402
from helm_core.knowledge.parsers import (  # noqa: E402
    MAX_DOMINANT_CHAR_RATIO,
    _dominant_char_ratio,
    _quality_ok,
    parse_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


# ── §14.6 quality gate — пороги ──────────────────────────────────────────

def test_quality_ok_accepts_real_document_text():
    assert _quality_ok("Решение: используем Postgres для HELM Knowledge.")


def test_quality_ok_rejects_empty_or_near_empty():
    assert not _quality_ok("")
    assert not _quality_ok("   ")
    assert not _quality_ok("ok")


def test_quality_ok_rejects_replacement_characters():
    assert not _quality_ok("���������������������")


def test_dominant_char_ratio_separates_broken_font_from_real_text():
    """НАЙДЕНО живым тестом: PDF с шрифтом без кириллицы (Helvetica)
    извлекается не как U+FFFD, а как валидный, но испорченный текст —
    кириллица схлопывается в повторяющуюся букву. Реальные числа:
    сломанный текст 0.346, настоящие документы 0.105-0.154 (см. docstring
    MAX_DOMINANT_CHAR_RATIO в parsers.py)."""
    broken = "HELM Knowledge: pdf fixture test.\nnnnnnnn: nnnnnnnnnn Postgres.\n\n"
    real = "Решение: используем Postgres для HELM Knowledge."

    assert _dominant_char_ratio(broken) > MAX_DOMINANT_CHAR_RATIO
    assert _dominant_char_ratio(real) < MAX_DOMINANT_CHAR_RATIO
    assert not _quality_ok(broken)
    assert _quality_ok(real)


# ── parse_file() — реальные файлы, реальный MarkItDown ───────────────────

@pytest.mark.parametrize("filename, expected_substring", [
    ("sample.txt", "Простой текстовый файл"),
    ("sample.docx", "Решение: используем Postgres"),
    ("sample.pptx", "Встречу перенесли на четверг"),
    ("sample.xlsx", "Выручка"),
    ("sample.csv", "foo"),
    ("sample_clean.pdf", "Решение: используем Postgres"),
])
def test_parse_file_fast_path_succeeds_on_real_documents(filename, expected_substring):
    result = parse_file(FIXTURES / filename)

    assert result.parser == "markitdown"
    assert result.quality_ok is True
    assert expected_substring in result.text


def test_parse_file_escalates_when_fast_path_extraction_is_broken():
    """sample_broken_font.pdf нарисован шрифтом без кириллицы (Helvetica)
    — MarkItDown извлекает валидный, но испорченный текст, quality gate
    это ловит, роутер эскалирует на Docling. Сам Docling не установлен в
    этом окружении — эскалация обязана попытаться его импортировать и
    провалиться понятной ошибкой (ModuleNotFoundError), а не молча
    вернуть плохой markitdown-текст как будто он прошёл gate."""
    with pytest.raises(ModuleNotFoundError):
        parse_file(FIXTURES / "sample_broken_font.pdf")


# ── аудио/видео → GigaAM (§14.7, ADR-021) ─────────────────────────────────
#
# gigaam/torch/silero_vad не установлены в этом окружении (тяжёлые
# зависимости воркера, живой замер — только на сервере, см. ADR-021) —
# transcribe_audio() мокается, тестируется РОУТИНГ parse_file(), не сама
# транскрипция.

@pytest.mark.parametrize("filename", [
    "voice.ogg", "note.oga", "clip.opus", "song.mp3", "audio.wav",
    "track.m4a", "lossless.flac", "video.mp4", "movie.mov", "clip.webm",
])
def test_is_audio_file_recognizes_audio_and_video_extensions(filename):
    assert is_audio_file(Path(filename))


@pytest.mark.parametrize("filename", ["sample.docx", "sample.txt", "sample.pdf"])
def test_is_audio_file_rejects_document_extensions(filename):
    assert not is_audio_file(Path(filename))


def test_parse_file_routes_audio_to_gigaam_before_markitdown(monkeypatch):
    monkeypatch.setattr(parsers, "transcribe_audio",
                        lambda path: "Встречу перенесли на четверг, предупредите клиента.")

    result = parse_file(Path("voice.ogg"))

    assert result.parser == "gigaam"
    assert result.quality_ok is True
    assert "четверг" in result.text


def test_parse_file_audio_empty_transcript_is_needs_review(monkeypatch):
    """Пустая/бессмысленная расшифровка (тишина, нераспознанная речь) —
    тот же quality gate, что у документов, не отдельная логика."""
    monkeypatch.setattr(parsers, "transcribe_audio", lambda path: "")

    result = parse_file(Path("voice.ogg"))

    assert result.parser == "gigaam"
    assert result.quality_ok is False


# ── fb2: измеренный дефект 07.09.2026 ────────────────────────────────────

def test_markitdown_alone_returns_fb2_markup_and_passes_the_quality_gate():
    """ПРИЧИНА, по которой у fb2 своя ветка — не вкус, а замер на книге
    владельца: у MarkItDown нет конвертера fb2, файл распознаётся как
    text/xml и возвращается как есть. Разметка при этом проходит
    `_quality_ok()` (валидный текст, нет U+FFFD), поэтому эскалации на
    Docling не происходит, и в базу знаний ложатся теги."""
    raw = parsers._parse_with_markitdown(FIXTURES / "sample.fb2")

    assert "<FictionBook" in raw
    assert _quality_ok(raw) is True


def test_markitdown_fb2_becomes_one_chunk_for_the_whole_book():
    """Вторая половина того же дефекта: в XML нет ни одной пустой
    строки, а `rechunk()` режет только по ним — вся книга становилась
    ОДНИМ фрагментом. Именно это владелец увидел как «сохранено
    фрагментов: 1»."""
    raw = parsers._parse_with_markitdown(FIXTURES / "sample.fb2")

    assert len(rechunk(raw)) == 1


def test_parse_file_reads_fb2_structure_instead_of_markup():
    result = parse_file(FIXTURES / "sample.fb2")

    assert result.parser == "fb2"
    assert result.quality_ok is True
    assert "<p>" not in result.text and "<FictionBook" not in result.text
    # Разделы — заголовками по глубине вложенности, а не одним потоком.
    assert "# Записки о погоде" in result.text
    assert "## Часть первая Начало" in result.text
    assert "### Глава о дожде" in result.text
    # Разметка внутри абзаца не рвёт слово и не добавляет пробелов.
    assert "этот сорокадневный дождь" in result.text


def test_parse_file_fb2_keeps_author_and_drops_service_fields():
    result = parse_file(FIXTURES / "sample.fb2")

    assert "Автор: Иван Петров" in result.text
    assert "example.com" not in result.text


def test_fb2_chapters_become_separate_chunks_under_their_headings():
    """Ради чего всё: каждая глава — своя единица поиска со своим
    заголовком, а не кусок общего блока."""
    chunks = rechunk(parse_file(FIXTURES / "sample.fb2").text)

    assert len(chunks) > 1
    rain = [c for c in chunks if "сорок дней" in c]
    assert len(rain) == 1
    assert rain[0].startswith("### Глава о дожде")
    assert "Снег ложится" not in rain[0]
