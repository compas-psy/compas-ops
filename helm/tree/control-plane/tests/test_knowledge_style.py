"""helm_core/knowledge/style.py — снимок личного стиля владельца для Z2.

Сам снимок (текст промпта) не тестируется на содержание — это данные,
не логика; тестируется только контракт версии, на который опирается
rephrase.py (ещё не написан, см. ADR по Z2).
"""

from helm_core.knowledge.style import OWNER_STYLE_PROMPT, OWNER_STYLE_VERSION, style_prompt_for_version


def test_style_prompt_for_current_version_returns_the_prompt():
    assert style_prompt_for_version(OWNER_STYLE_VERSION) == OWNER_STYLE_PROMPT


def test_style_prompt_for_unknown_version_is_none():
    assert style_prompt_for_version(OWNER_STYLE_VERSION + 1) is None


def test_style_prompt_is_non_empty_text():
    assert isinstance(OWNER_STYLE_PROMPT, str)
    assert len(OWNER_STYLE_PROMPT) > 0
