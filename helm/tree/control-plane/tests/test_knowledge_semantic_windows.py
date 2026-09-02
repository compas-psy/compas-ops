"""v4.0 §14.4.1 — разбиение источника на окна.

Один инвариант важнее всех остальных: КАЖДЫЙ непробельный символ
источника попадает ровно в одно окно. Он и есть содержание запрета
«никакой немой `text[:4000]` отсечки» — всё прочее (по заголовкам ли
резать, по абзацам ли) вопрос качества, а этот — вопрос честности.

Тесты параметризованы по формам текста, а не написаны на один пример:
дефект «первая страница разобрана, остальное молча пропало» проявляется
именно на неудобной форме — таблице без точек, расшифровке одним куском,
документе вообще без заголовков.
"""

import pytest

from helm_core.knowledge.semantic_windows import (
    WINDOW_MAX_CHARS, WINDOW_MIN_CHARS, build_windows, split_window,
)

SHAPES = {
    "с заголовками": "# Приём\n\nПервый абзац.\n\n## Анализы\n\n" + "Строка про показатель. " * 300,
    "без заголовков": "Абзац один.\n\nАбзац два.\n\n" + "слово " * 2000,
    "одно предложение без точек": "а" * 9000,
    "таблица одним куском": "| поле | значение |\n" * 600,
    "короткий": "Одна строка и всё.",
    "вложенные заголовки": "# А\n\nтекст А\n\n## Б\n\nтекст Б\n\n### В\n\nтекст В\n\n# Г\n\nтекст Г",
    "пустой": "   \n\n  ",
}


@pytest.mark.parametrize("shape", sorted(SHAPES), ids=lambda s: s)
def test_every_character_lands_in_exactly_one_window(shape) -> None:
    text = SHAPES[shape]
    windows = build_windows(text)

    seen: dict[int, int] = {}
    for window in windows:
        for position in range(window.char_start, window.char_end):
            assert position not in seen, (
                f"символ {position} попал и в окно {seen[position]}, и в {window.ordinal}")
            seen[position] = window.ordinal

    lost = [i for i, char in enumerate(text) if not char.isspace() and i not in seen]
    assert not lost, f"{len(lost)} непробельных символов не попали ни в одно окно"


@pytest.mark.parametrize("shape", sorted(SHAPES), ids=lambda s: s)
def test_window_text_matches_its_offsets(shape) -> None:
    """Смещения — не украшение: из них собирается происхождение
    упоминания. Разойдись они с текстом, и цитата в ответе указывала бы
    не туда."""
    text = SHAPES[shape]
    for window in build_windows(text):
        assert window.text == text[window.char_start:window.char_end]


@pytest.mark.parametrize("shape", sorted(SHAPES), ids=lambda s: s)
def test_windows_go_in_order_and_respect_the_limit(shape) -> None:
    windows = build_windows(SHAPES[shape])
    assert [w.ordinal for w in windows] == list(range(len(windows)))
    for earlier, later in zip(windows, windows[1:]):
        assert earlier.char_end <= later.char_start
    assert all(len(w.text) <= WINDOW_MAX_CHARS for w in windows)


def test_heading_path_accumulates_by_level() -> None:
    """`## Б` внутри `# А` даёт путь ('А', 'Б'), а следующий `# Г`
    сбрасывает вложенность. Без этого абзац «в норме» терял бы раздел, в
    котором он стоит, — а вместе с ним и смысл."""
    windows = build_windows(SHAPES["вложенные заголовки"])
    paths = [w.heading_path for w in windows]

    assert ("А",) in paths
    assert ("А", "Б") in paths
    assert ("А", "Б", "В") in paths
    assert ("Г",) in paths


def test_split_divides_a_window_and_keeps_its_span() -> None:
    """Деление переполненного окна (§14.4.1) не должно терять текст:
    границы детей обязаны покрывать границы родителя."""
    text = SHAPES["без заголовков"]
    # Самое длинное окно, а не первое: первое здесь — короткая пара
    # абзацев, и делить её справедливо нечего.
    parent = max(build_windows(text), key=lambda w: len(w.text))

    children = split_window(parent)

    assert len(children) > 1
    assert children[0].char_start == parent.char_start
    assert children[-1].char_end == parent.char_end
    joined = "".join(c.text for c in children)
    assert joined.replace(" ", "") == parent.text.replace(" ", "")


def test_a_window_too_short_to_split_is_returned_unchanged() -> None:
    """Вызывающий обязан отличить «поделили» от «делить нечего», иначе
    зациклится на окне, которое переполняется и не делится."""
    parent = build_windows("Короткое окно, делить нечего.")[0]
    assert split_window(parent) == [parent]


def test_the_limit_is_not_a_silent_cut() -> None:
    """Предел окна — граница разбиения, а не обрезки. Текст вдвое длиннее
    предела обязан дать несколько окон, а не одно усечённое."""
    text = "слово " * 2000
    windows = build_windows(text, limit=WINDOW_MIN_CHARS * 2)

    assert len(windows) > 1
    assert sum(len(w.text) for w in windows) >= len(text.strip())
