"""Платный переход на пользовательском входе: умолчание закрыто.

ДЕФЕКТ (разбор владельца 07.09.2026). `probe(paid_allowed=False)`
защищал память только на бумаге: Telegram-плагин звал probe как
`paid_allowed=not in_memory_conversation`, а `in_memory_conversation`
читался из ВНУТРИПРОЦЕССНОГО словаря последних ходов. Перезапуск шлюза
стирал словарь — и разговор, целиком состоявший из вопросов к
собственным записям, снова получал право уйти в платную модель.
Потерянный контекст становился разрешением.

Здесь проверяется, что разрешение теперь: (1) лежит в базе и переживает
перезапуск, (2) по умолчанию закрыто, (3) включается только явной
командой, (4) не выводится из формулировки вопроса.
"""

from __future__ import annotations

import pytest

from helm_core.knowledge.chat_mode import (
    DEFAULT_MODE, detect_mode_command, get_mode, paid_allowed_for, set_mode,
)
from helm_core.models import KnowledgeChatModeValue

CHAT = {"channel": "telegram", "chat_id": "424242"}


def test_an_unknown_chat_may_not_pay(session):
    """Отсутствие контекста не является разрешением."""
    assert DEFAULT_MODE == KnowledgeChatModeValue.MEMORY
    assert get_mode(session, **CHAT) == KnowledgeChatModeValue.MEMORY
    assert paid_allowed_for(session, **CHAT) is False


def test_a_nameless_caller_may_not_pay(session):
    """Вход, который не может сказать, кто спрашивает, не может и тратить."""
    assert paid_allowed_for(session, channel=None, chat_id=None) is False
    assert paid_allowed_for(session, channel="telegram", chat_id=None) is False


def test_the_mode_survives_a_restart(session):
    """Режим лежит в базе, а не в памяти процесса — в этом вся правка.

    «Перезапуск» здесь — новая сессия к той же базе: ровно то, что
    оставалось от состояния после рестарта шлюза, то есть ничего.
    """
    set_mode(session, **CHAT, mode=KnowledgeChatModeValue.PAID)
    session.commit()
    session.expunge_all()
    assert paid_allowed_for(session, **CHAT) is True

    set_mode(session, **CHAT, mode=KnowledgeChatModeValue.MEMORY)
    session.commit()
    session.expunge_all()
    assert paid_allowed_for(session, **CHAT) is False, (
        "местный режим не пережил перезапуск — вернулась исходная дыра")


def test_modes_do_not_leak_between_chats(session):
    set_mode(session, channel="telegram", chat_id="1",
             mode=KnowledgeChatModeValue.PAID)
    session.flush()
    assert paid_allowed_for(session, channel="telegram", chat_id="2") is False
    assert paid_allowed_for(session, channel="max", chat_id="1") is False


@pytest.mark.parametrize("text", [
    "можно платно",
    "Разреши платные ответы",
    "платный режим",
])
def test_explicit_permission_turns_paid_on(text):
    assert detect_mode_command(text) == KnowledgeChatModeValue.PAID


@pytest.mark.parametrize("text", [
    "только память",
    "Только моя память",
    "запрети платные ответы",
    "платный режим выключить",
])
def test_explicit_refusal_turns_paid_off(text):
    assert detect_mode_command(text) == KnowledgeChatModeValue.MEMORY


@pytest.mark.parametrize("text", [
    "что я беру с собой из лекарств?",
    "в каком порядке я всё делаю по прилёте?",
    "какой у меня был холестерин в последний раз?",
    "напомни, можно платно было спросить или нет?",
    "по книге Линде что такое ЭОТ?",
    "",
])
def test_an_ordinary_question_never_switches_the_mode(text):
    """Режим — команда, а не признак в тексте.

    Прогон 422: словарь слов-признаков отнёс «что я беру с собой из
    лекарств?» к общим вопросам и выдал право оплатить ответ о
    собственных записях владельца. Владелец запретил чинить это
    расширением словаря; значит и переключатель не должен быть
    словарём, срабатывающим внутри обычной фразы.
    """
    assert detect_mode_command(text) is None


# ── вход не может открыть себе оплату мимо режима ─────────────────────

def test_named_chat_overrides_whatever_the_caller_asked_for(session):
    """`paid_allowed: true` от пользовательского входа обязан быть проигнорирован.

    Иначе политика держится на дисциплине вызывающего, а вызывающий —
    это плагин в чужом процессе, который уже один раз ошибся.
    """
    from helm_core.api.internal import KnowledgeProbeIn, knowledge_probe

    body = KnowledgeProbeIn(query="какой у меня был холестерин?",
                            channel="telegram", chat_id="99", paid_allowed=True)
    result = knowledge_probe(body, session=session)
    assert result["outcome"] != "NEEDS_REASONING", (
        "вход открыл себе платный переход мимо режима чата")


def test_the_mode_command_is_answered_and_stored(session):
    from helm_core.api.internal import KnowledgeProbeIn, knowledge_probe

    result = knowledge_probe(
        KnowledgeProbeIn(query="можно платно", channel="telegram", chat_id="77"),
        session=session)
    assert result["outcome"] == "LOCAL_ANSWER"
    assert "платные" in result["answer_text"].lower()
    assert paid_allowed_for(session, channel="telegram", chat_id="77") is True

    knowledge_probe(
        KnowledgeProbeIn(query="только память", channel="telegram", chat_id="77"),
        session=session)
    assert paid_allowed_for(session, channel="telegram", chat_id="77") is False


def test_the_telegram_plugin_no_longer_computes_the_right_to_pay():
    """Сторожевой тест: право платить не возвращается в плагин.

    Плагин живёт в чужом процессе и в этом репозитории не покрыт
    тестами. Единственное, что здесь проверяемо, — что в вызове probe
    больше нет `paid_allowed`, вычисленного из состояния этого процесса.
    """
    import pathlib

    plugin = (pathlib.Path(__file__).resolve().parents[2]
              / "hermes/plugins/helm-control/__init__.py")
    body = "\n".join(
        line for line in plugin.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#"))
    assert "paid_allowed=not in_memory_conversation" not in body, (
        "потерянный контекст снова стал разрешением платить")
    assert '"paid_allowed"' not in body, (
        "плагин снова отправляет право платить сам, мимо режима чата")
    assert '"chat_id"' in body and '"channel"' in body, (
        "плагин не называет чат — Control Plane не сможет применить режим")
