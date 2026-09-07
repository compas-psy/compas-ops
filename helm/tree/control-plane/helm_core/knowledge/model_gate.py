"""Ворота к локальной модели: живой вопрос вперёд фоновой порции.

ЗАЧЕМ. Замер 07.09.2026: пока шёл разбор книги (1399 фрагментов),
`gemma2:2b` отвечал на живой вопрос за 48 секунд при пороге 45 — то есть
каждый ответ в этот момент деградировал до упрощённого. Ollama держит
одну модель и обслуживает вызовы по очереди; фоновый разбор занимал её
непрерывно. Как только разбор сняли, время ответа вернулось к 5 секундам.

Продление аренды задания этого не решает: аренда говорит, кто владеет
заданием, а не кто владеет моделью. Увеличение таймаута ответа тоже не
решает — оно превращает «ответ не пришёл» в «ответ пришёл через минуту».

ЧТО ЗДЕСЬ ОБЕЩАНО, А ЧТО НЕТ. Обещано: живой вопрос ждёт не дольше
ОДНОГО вызова модели, а не всей книги. Не обещано вытеснение уже
идущего вызова — прервать запрос к модели на середине нельзя, и делать
вид, что можно, было бы враньём в докстринге.

ПОЧЕМУ flock, А НЕ advisory-локи Postgres. `/opt/helm-knowledge`
смонтирован и в `helm-core`, и в `helm-knowledge-worker` (один bind с
хоста), так что flock виден обоим процессам. Это не требует ни лишнего
соединения с базой на каждый вызов модели, ни протаскивания сессии в
`synthesis.py`, который её сейчас не получает вовсе. Ядро снимает flock
само при смерти процесса — то самое свойство, ради которого заданиям
понадобилась аренда.

ВОРОТА НИКОГДА НЕ РОНЯЮТ ОТВЕТ. Каталог недоступен на запись, файл не
создался, лок не взялся за отведённое время — вызов идёт как шёл. Ворота
ускоряют, а не разрешают: память, которая молчит из-за сломанного лока,
хуже памяти, которая отвечает медленно.
"""

from __future__ import annotations

import fcntl
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

GATE_DIR = Path(os.environ.get("HELM_MODEL_GATE_DIR", "/opt/helm-knowledge/.model-gate"))

#: Один вызов модели за раз. Держится на время самого HTTP-запроса.
MODEL_LOCK = "model.lock"
#: Признак «живой вопрос сейчас идёт». Живые берут его совместно
#: (их может быть несколько), фон проверяет исключительно — то есть
#: «занят кем угодно» для фона означает «уступи».
INTERACTIVE_LOCK = "interactive.lock"

#: Сколько фон готов уступать подряд, прежде чем всё-таки взять модель.
#: Без предела книга не дочитается никогда, если вопросы идут потоком:
#: отзывчивость важнее, но «никогда» — не приемлемый срок обработки.
BACKGROUND_YIELD_SECONDS = 60.0
#: Пауза фона после каждого вызова. Даёт живому вопросу выиграть гонку
#: за модель, не дожидаясь следующей проверки намерения.
BACKGROUND_COOLDOWN_SECONDS = 2.0
#: Сколько живой вопрос ждёт освобождения модели, прежде чем пойти
#: всё равно. Дольше ждать нечего: очередь внутри Ollama даст ровно то
#: же самое, но без нашего ожидания сверху.
INTERACTIVE_WAIT_SECONDS = 8.0

_POLL_SECONDS = 0.25


_unavailable_logged = False


def _open(name: str):
    """Дескриптор файла-замка либо None, если ворота недоступны."""
    global _unavailable_logged
    try:
        GATE_DIR.mkdir(parents=True, exist_ok=True)
        return os.open(str(GATE_DIR / name), os.O_RDWR | os.O_CREAT, 0o660)
    except OSError as exc:
        if not _unavailable_logged:
            # Один раз: недоступные ворота — это состояние среды, а не
            # событие каждого вызова, и строка на каждое окно книги
            # утопила бы журнал в шуме.
            logger.warning("ворота модели недоступны (%s), вызовы идут без них", exc)
            _unavailable_logged = True
        return None


def _acquire(fd: int, flags: int, *, deadline: float) -> bool:
    """Взять замок до срока. `False` — не успели, решает вызывающий."""
    while True:
        try:
            fcntl.flock(fd, flags | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(_POLL_SECONDS)


@contextmanager
def interactive_call():
    """Живой вопрос: заявляет намерение и старается взять модель первым.

    Намерение держится всё время вызова — по нему фон понимает, что
    уступать надо не мгновение, а пока владелец разговаривает с памятью.
    """
    intent = _open(INTERACTIVE_LOCK)
    model = _open(MODEL_LOCK)
    held_intent = held_model = False
    try:
        if intent is not None:
            held_intent = _acquire(intent, fcntl.LOCK_SH,
                                   deadline=time.monotonic() + _POLL_SECONDS)
        if model is not None:
            held_model = _acquire(model, fcntl.LOCK_EX,
                                  deadline=time.monotonic() + INTERACTIVE_WAIT_SECONDS)
            if not held_model:
                logger.info("модель занята дольше %.0f с — живой вопрос идёт без ворот",
                            INTERACTIVE_WAIT_SECONDS)
        yield
    finally:
        _release(model, held_model)
        _release(intent, held_intent)


@contextmanager
def background_call():
    """Фоновая порция: уступает живым вопросам, потом занимает модель."""
    intent = _open(INTERACTIVE_LOCK)
    if intent is not None:
        # Замок намерения берётся только чтобы УЗНАТЬ, идёт ли живой
        # вопрос, и сразу отпускается: держать его фоном значило бы не
        # пускать к модели тех, ради кого мы уступаем.
        free = _acquire(intent, fcntl.LOCK_EX, deadline=time.monotonic())
        if not free:
            free = _acquire(intent, fcntl.LOCK_EX,
                            deadline=time.monotonic() + BACKGROUND_YIELD_SECONDS)
            logger.info("фоновая порция уступала живым вопросам, %s",
                        "дождалась" if free else
                        f"предел {BACKGROUND_YIELD_SECONDS:.0f} с исчерпан, идёт всё равно")
        _release(intent, free)

    model = _open(MODEL_LOCK)
    held = model is not None and _acquire(model, fcntl.LOCK_EX,
                                          deadline=time.monotonic() + BACKGROUND_YIELD_SECONDS)
    try:
        yield
    finally:
        _release(model, held)
        # Пауза ПОСЛЕ снятия замка: пока она идёт, модель свободна.
        time.sleep(BACKGROUND_COOLDOWN_SECONDS)


def _release(fd: int | None, held: bool) -> None:
    if fd is None:
        return
    try:
        if held:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
