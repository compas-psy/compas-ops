"""Ворота к локальной модели: живой вопрос вперёд фоновой порции.

Дефект, который эти тесты стерегут (замер 07.09.2026): пока шёл разбор
книги, живой ответ шёл 48 секунд при пороге 45 — фоновый разбор занимал
единственную модель непрерывно. Проверяется поведение ворот, а не время
ответа: время зависит от машины, поведение — нет.
"""

from __future__ import annotations

import threading
import time

import pytest

from helm_core.knowledge import model_gate


@pytest.fixture()
def gate(tmp_path, monkeypatch):
    monkeypatch.setattr(model_gate, "GATE_DIR", tmp_path / "gate")
    monkeypatch.setattr(model_gate, "BACKGROUND_COOLDOWN_SECONDS", 0.0)
    monkeypatch.setattr(model_gate, "_POLL_SECONDS", 0.01)
    return model_gate


def test_background_yields_while_a_live_question_is_running(gate):
    """Главное свойство: фон ждёт, пока владелец разговаривает с памятью."""
    live_started = threading.Event()
    release_live = threading.Event()

    def live():
        with gate.interactive_call():
            live_started.set()
            release_live.wait(5)

    thread = threading.Thread(target=live, daemon=True)
    thread.start()
    assert live_started.wait(5)

    started = time.monotonic()
    holding = threading.Event()

    def background():
        with gate.background_call():
            holding.set()

    worker = threading.Thread(target=background, daemon=True)
    worker.start()
    assert not holding.wait(0.4), "фон занял модель, не уступив живому вопросу"

    release_live.set()
    assert holding.wait(5), "фон не пошёл после того, как живой вопрос закончился"
    assert time.monotonic() - started >= 0.4
    thread.join(5)
    worker.join(5)


def test_background_does_not_wait_forever(gate, monkeypatch):
    """Уступка ограничена: иначе книга не дочитается при потоке вопросов."""
    monkeypatch.setattr(gate, "BACKGROUND_YIELD_SECONDS", 0.3)
    release_live = threading.Event()
    live_started = threading.Event()

    def live():
        with gate.interactive_call():
            live_started.set()
            release_live.wait(5)

    thread = threading.Thread(target=live, daemon=True)
    thread.start()
    assert live_started.wait(5)

    started = time.monotonic()
    with gate.background_call():
        waited = time.monotonic() - started
    assert 0.3 <= waited < 3, f"фон уступал {waited:.2f} с вместо предела 0.3"

    release_live.set()
    thread.join(5)


def test_live_question_does_not_wait_out_a_long_background_call(gate, monkeypatch):
    """Живой вопрос идёт всё равно, если модель занята дольше предела.

    Очередь внутри Ollama даст ровно то же самое; ждать сверх этого
    значит превращать «медленно» в «никогда».
    """
    monkeypatch.setattr(gate, "INTERACTIVE_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(gate, "BACKGROUND_YIELD_SECONDS", 0.1)
    release_bg = threading.Event()
    bg_started = threading.Event()

    def background():
        with gate.background_call():
            bg_started.set()
            release_bg.wait(5)

    thread = threading.Thread(target=background, daemon=True)
    thread.start()
    assert bg_started.wait(5)

    started = time.monotonic()
    with gate.interactive_call():
        waited = time.monotonic() - started
    assert waited < 2, f"живой вопрос прождал {waited:.2f} с вместо предела 0.3"

    release_bg.set()
    thread.join(5)


def test_broken_gate_never_blocks_a_call(tmp_path, monkeypatch):
    """Недоступные ворота обязаны пропускать вызов, а не ронять его."""
    monkeypatch.setattr(model_gate, "GATE_DIR", tmp_path / "file" / "gate")
    (tmp_path / "file").write_text("не каталог", encoding="utf-8")
    monkeypatch.setattr(model_gate, "BACKGROUND_COOLDOWN_SECONDS", 0.0)

    with model_gate.interactive_call():
        pass
    with model_gate.background_call():
        pass
