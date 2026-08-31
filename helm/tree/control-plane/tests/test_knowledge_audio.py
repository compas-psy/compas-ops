"""helm_core/knowledge/audio.py — только чистые функции без gigaam/torch.

`transcribe_audio()` (тяжёлые зависимости) проверяется на живом сервере
(`scripts/verify-gigaam-audio-pipeline.sh`, ADR-021), не здесь — та же
причина, по которой Docling-путь `parsers.py` не покрыт локальными
тестами (см. `test_knowledge_parsers.py`).
"""

from helm_core.knowledge.audio import strip_timestamps


def test_strip_timestamps_removes_single_line_prefix():
    assert strip_timestamps("[0s] Запомни купить молоко") == "Запомни купить молоко"


def test_strip_timestamps_joins_multiple_lines_with_space():
    text = "[0s] Первая строка\n[12s] Вторая строка"
    assert strip_timestamps(text) == "Первая строка Вторая строка"


def test_strip_timestamps_only_strips_prefix_at_line_start():
    """Число в скобках ВНУТРИ текста строки (не в самом начале) — часть
    содержимого, не таймкод, убирать не должно."""
    assert strip_timestamps("[3s] Встреча в [12] переговорке") == "Встреча в [12] переговорке"


def test_strip_timestamps_empty_text():
    assert strip_timestamps("") == ""
