"""Единица поиска: то, из-за чего ответы были бредовыми.

Каждая проверка ниже — измеренный дефект из разбора 05.09.2026
(`docs/CHUNKING_AND_BAD_ANSWERS_2026-09-05.md`, прогоны 307 и 309), а не
придуманный случай. Строки бланка взяты оттуда же дословно.

Базы не нужно: правило разбиения — свойство текста.
"""

from __future__ import annotations

import ast
import pathlib

from helm_core.knowledge import ingest as ingest_mod
from helm_core.knowledge import worker as worker_mod
from helm_core.knowledge.chunking import MIN_CHUNK_CHARS, rechunk

#: Кусок бланка ровно той формы, что дала пять одинаковых кандидатов и
#: заняла весь колчан доказательств.
BLANK_FORM = """ОСМОТР ЭНДОКРИНОЛОГА

Врач: Безручко Дарья Юрьевна __________________

Дата: 24.08.2026 09:58

Жалобы на утомляемость и жажду в течение последних трёх месяцев, вес
стабильный, аппетит сохранён, ночная жажда беспокоит.
"""


def test_heading_is_never_a_chunk_on_its_own():
    """«ОСМОТР ЭНДОКРИНОЛОГА» без осмотра — не ответ ни на один вопрос."""
    chunks = rechunk(BLANK_FORM)
    assert "ОСМОТР ЭНДОКРИНОЛОГА" not in chunks
    assert chunks[0].startswith("ОСМОТР ЭНДОКРИНОЛОГА\n")


def test_form_line_is_not_a_chunk_on_its_own():
    """«Дата: 24.08.2026 09:58» отдельным чанком была оторвана от
    события, к которому относится."""
    chunks = rechunk(BLANK_FORM)
    assert "Дата: 24.08.2026 09:58" not in chunks
    assert any("Дата: 24.08.2026 09:58" in chunk for chunk in chunks)


def test_short_blocks_are_glued_until_the_minimum():
    text = "\n\n".join(f"строка бланка {i}" for i in range(20))
    chunks = rechunk(text)
    assert all(len(chunk) >= MIN_CHUNK_CHARS for chunk in chunks[:-1])


def test_a_new_heading_stops_the_glue():
    """Иначе поиск начнёт отвечать соседним разделом."""
    text = ("ОСМОТР ЭНДОКРИНОЛОГА\n\nЖалоб нет.\n\n"
            "ОСМОТР ГАСТРОЭНТЕРОЛОГА\n\nБоли в правом подреберье.")
    chunks = rechunk(text)
    assert len(chunks) == 2
    assert "ГАСТРОЭНТЕРОЛОГА" not in chunks[0]
    assert "Жалоб нет" not in chunks[1]


def test_markdown_heading_counts_too():
    chunks = rechunk("## Заключение\n\nПатологии не выявлено.")
    assert chunks == ["## Заключение\nПатологии не выявлено."]


def test_a_long_paragraph_stays_one_chunk():
    """Склейка нужна короткому. Нормальный абзац трогать незачем."""
    paragraph = "Полноценный абзац текста. " * 12
    assert rechunk(paragraph.strip()) == [paragraph.strip()]


def test_table_arrives_as_one_block_and_stays_one():
    """В Markdown таблица идёт без пустых строк, значит и в чанк должна
    попасть целиком — вместе с шапкой столбцов, иначе значение теряет
    название показателя."""
    table = ("| Показатель | Значение | Норма |\n"
             "| --- | --- | --- |\n"
             "| Гемоглобин | 145 | 130-160 |\n"
             "| Лейкоциты | 6.2 | 4.0-9.0 |")
    chunks = rechunk("## Общий анализ крови\n\n" + table)
    assert len(chunks) == 1
    assert "Показатель" in chunks[0] and "Лейкоциты" in chunks[0]


def test_chunk_text_is_not_rewritten():
    """Чанк обязан совпадать с источником посимвольно: иначе цитата в
    ответе перестанет быть цитатой."""
    for chunk in rechunk(BLANK_FORM):
        for line in chunk.splitlines():
            if line.strip():
                assert line in BLANK_FORM


def test_empty_text_gives_no_chunks():
    """Раньше пустой текст давал чанк из пустой строки — единицу поиска
    ни о чём."""
    assert rechunk("") == []
    assert rechunk("   \n\n  ") == []


def test_trailing_heading_is_not_lost_silently():
    chunks = rechunk("Текст первого раздела.\n\nЗАКЛЮЧЕНИЕ")
    assert "ЗАКЛЮЧЕНИЕ" in chunks


# ── проводка: одна нарезка на все пути ───────────────────────────────
#
# Через AST, а не поиском подстроки: упоминание в комментарии не вызов.
# Тот же приём, что в `test_knowledge_semantic_jobs.py`.

def _calls_in(module, func_name: str) -> set[str]:
    tree = ast.parse(pathlib.Path(module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return {
                child.func.id if isinstance(child.func, ast.Name) else child.func.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, (ast.Name, ast.Attribute))
            }
    raise AssertionError(f"нет функции {func_name}")


def test_both_ingest_paths_use_the_same_chunker():
    """Загрузка текстом и загрузка файлом обязаны нарезать одинаково.
    Две копии одной нарезки разойдутся при первой же правке."""
    assert "store_chunks" in _calls_in(ingest_mod, "ingest_text")
    assert "store_chunks" in _calls_in(worker_mod, "process_job")


def test_the_old_blank_line_splitter_is_gone():
    """Иначе он останется вторым правилом нарезки, которое кто-нибудь
    позовёт по привычке."""
    assert not hasattr(ingest_mod, "split_chunks")
