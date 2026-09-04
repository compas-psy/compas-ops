"""R5 — продакшн-путь и точное происхождение, без базы.

Механику публикации целиком проверяет `test_knowledge_semantic_v3_publish.py`,
но ему нужен Postgres. Здесь — те две вещи из R5, которые доказываются
чистыми функциями и потому проверяются всегда, в том числе там, где базы
нет:

    продакшн зовёт путь, аттестованный R4 (node-only + компилятор рёбер)
    диапазон упоминания — место цитаты в источнике (§30.8.5 F «exact span»)
"""

from __future__ import annotations

import inspect

from helm_core.knowledge import semantic_publish
from helm_core.knowledge.semantic_extract import extract_nodes_window
from helm_core.knowledge.semantic_windows import SemanticWindow


def _window(text: str, *, char_start: int = 0) -> SemanticWindow:
    return SemanticWindow(ordinal=0, text=text, char_start=char_start,
                          char_end=char_start + len(text), heading_path=())


class TestProductionUsesTheAttestedPath:
    """R5.1: до R5 продакшн звал `extract_window()` (старая схема с edges) и
    писал рёбра модели напрямую — то есть выкатывал НЕ тот путь, который
    мерили гейты R4."""

    def test_default_extractor_is_the_node_only_one(self):
        default = inspect.signature(
            semantic_publish.publish_semantic_run).parameters["extract"].default
        assert default is extract_nodes_window

    def test_edges_come_from_the_compiler_not_from_the_model(self):
        source = inspect.getsource(semantic_publish._process)
        assert "compile_relations(" in source, "рёбра обязан строить компилятор"


class TestExactSpan:
    """R5.2 (§30.8.5 F): «resolves to exact source + ... span»."""

    def test_span_is_the_quote_position_not_the_window(self):
        window = _window("Приём вёл Иванов. Назначен контроль.")
        span = semantic_publish._locate_span("Назначен контроль.", window)
        assert span == (18, 36)
        assert window.text[span[0] - window.char_start:span[1] - window.char_start] == (
            "Назначен контроль.")

    def test_span_is_offset_by_the_window_start(self):
        """Окно редко начинается с нулевого символа источника: диапазон
        обязан указывать в ИСТОЧНИК, а не внутрь окна."""
        window = _window("Приём вёл Иванов.", char_start=4000)
        assert semantic_publish._locate_span("Иванов", window) == (4010, 4016)

    def test_quote_with_collapsed_whitespace_is_still_located(self):
        """`validate()` признаёт цитату обоснованной с точностью до
        пробелов, поэтому буквальный поиск нашёл бы не всё, что уже
        прошло grounding."""
        window = _window("Консультацию провёл\nИванов Пётр.")
        span = semantic_publish._locate_span("Консультацию провёл Иванов Пётр.", window)
        assert span == (0, 32)

    def test_unlocatable_quote_gives_no_span_instead_of_window_bounds(self):
        """Диапазон во всё окно неотличим от точного при чтении — то есть
        выглядел бы как происхождение, которого нет."""
        window = _window("Приём вёл Иванов.")
        assert semantic_publish._locate_span("Этого в окне нет", window) is None

    def test_empty_quote_gives_no_span(self):
        window = _window("Приём вёл Иванов.")
        assert semantic_publish._locate_span("", window) is None
        assert semantic_publish._locate_span("   ", window) is None
