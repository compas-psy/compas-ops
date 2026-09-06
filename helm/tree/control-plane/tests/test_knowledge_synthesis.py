"""Локальный синтез ответа из нескольких фрагментов (P4).

Сама модель здесь не участвует: проверяется разбор её ответа и —
главное — заземление, то есть по каким фрагментам ответ реально собран.
Качество модели меряется живьём, а не в unit-тесте.
"""

from helm_core.knowledge.synthesis import build_prompt, grounded_fragments, parse_response

CHANNELS = ("ссылки на мои каналы:\n"
            "Telegram: https://t.me/ilyamartynov_yourway\n"
            "B17.ru: https://www.b17.ru/eliah/")
TEA = "ссылку на видео про чай: https://www.facebook.com/share/r/1BuuXvqbyH/"
ENDOSCOPY = "Эзофагогастродуоденоскопия выполнена под местной анестезией."
FRAGMENTS = [CHANNELS, TEA, ENDOSCOPY]


def test_answer_without_the_format_line_is_still_grounded():
    """Живой прогон 388: на «Дай ссылку на мой канал B17» модель ответила
    по делу, но строку со ссылками не написала — и ответ отбрасывался
    целиком, а владелец получал «ближайшую цитату» про чай."""
    result = parse_response("Ссылка на ваш канал B17: https://www.b17.ru/eliah/",
                            fragments=FRAGMENTS)

    assert result is not None and result.answered
    assert result.used == (1,)


def test_model_reference_to_a_fragment_it_did_not_use_is_dropped():
    """Ссылки модели — её утверждение о себе, а не проверка. Решает
    содержание: пересечение непусто — берётся оно."""
    result = parse_response(
        "Ссылка: https://www.b17.ru/eliah/\nФРАГМЕНТЫ: 1,3", fragments=FRAGMENTS)

    assert result.used == (1,)


def test_answer_resting_on_nothing_shown_is_refused():
    """Проверить такой ответ нечем — показывать его нельзя."""
    assert parse_response("Общие рассуждения без единого совпадения",
                          fragments=FRAGMENTS) is None


def test_no_answer_is_an_outcome_not_a_failure():
    result = parse_response("НЕТ ОТВЕТА", fragments=FRAGMENTS)

    assert result is not None and result.answered is False


def test_one_accidental_word_does_not_outweigh_the_real_source():
    """Ответ про каналы совпадает с первым фрагментом двумя опорными
    словами и с чаем — одной случайной словоформой. Источником должен
    остаться первый."""
    answer = "Ваш канал B17: https://www.b17.ru/eliah/, ссылку смотрите там"

    assert grounded_fragments(answer, [CHANNELS, TEA]) == (1,)


def test_prompt_shows_every_fragment_numbered():
    prompt = build_prompt("какие у меня каналы", FRAGMENTS)

    assert "[1]" in prompt and "[2]" in prompt and "[3]" in prompt
    assert "какие у меня каналы" in prompt
