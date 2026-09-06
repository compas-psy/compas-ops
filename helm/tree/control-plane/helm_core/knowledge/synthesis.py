"""Ответ из нескольких найденных фрагментов — локально (§14.12, P4).

Распоряжение владельца 06.09.2026: «Несколько найденных фрагментов
должны позволять собрать ответ; автоматическая выдача ближайшей цитаты
при количестве находок больше одной не выполняет эту задачу».

ЧТО БЫЛО ДО. `_compose_answer()` отдавал ОДИН фрагмент: при единственной
находке — как ответ (Z0), при нескольких — как «ближайшее» (Z1). Выбор
делался рангом поиска, то есть совпадением слов, а не тем, отвечает ли
фрагмент на вопрос. Живой прогон 383 показал цену этого прямо: на
«какое у меня было давление?» первым по рангу встал протокол
эндоскопии, а консультация кардиолога с самим давлением стояла третьей
и в ответ не попала.

ЧТО ЗДЕСЬ. Локальная модель читает вопрос и все найденные фрагменты и
пишет ответ по ним. Три исхода, и они разные:

* ответ есть — модель обязана назвать номера фрагментов, по которым
  отвечает; без этой строки ответ не принимается (см. ниже);
* `answered=False` — модель прочитала фрагменты и говорит, что ответа в
  них нет. Это ОТВЕТ (честное отсутствие), а не сбой: вызывающий
  показывает «не нашёл» и перечисляет, что смотрел;
* `None` — модель недоступна, ответила пустотой или не выдержала
  формат. Про вопрос неизвестно ничего, вызывающий откатывается на
  прежний детерминированный composer (fail-open, тот же паттерн, что
  `rephrase_or_none()` и `embed_texts_or_none()`).

ПОЧЕМУ НОМЕРА ФРАГМЕНТОВ ОБЯЗАТЕЛЬНЫ. Модель маленькая (gemma2:2b) и
проверить её пересказ нечем: контроль здесь — не «похож ли текст на
источник», а «на что именно она ссылается». Ответ без ссылки не
показывается вовсе; ответ со ссылкой показывается вместе с ней, и
владелец может открыть названный документ и сверить. Это не гарантия от
выдумки внутри процитированного — это гарантия, что выдумку есть с чем
сверить. Ограничение известное, названное, не закрытое.

МОДЕЛЬ ТА ЖЕ, ЧТО У Z2-РЕФРАЗА. `gemma2:2b` выбран живым замером
31.08.2026 (docs/KNOWLEDGE_MODELS.md) как единственный из трёх без
языкового глюка на русском. Новый выбор модели здесь не делается: это
была бы новая серия исследований, которую владелец запретил прямо.
Потолок времени выше, чем у рефраза (фрагментов пять, а не один), и
измеряется живьём.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Тот же сервис и та же модель, что у Z2-рефраза (rephrase.py).
OLLAMA_URL = "http://ollama:11434/api/generate"
MODEL_NAME = "gemma2:2b"
#: Выше рефразного (20 c): на вход идёт до пяти фрагментов, а не один.
#: Верхняя граница согласована с PROBE_TIMEOUT плагина — вызов
#: синхронный, владелец ждёт ответа в мессенджере.
REQUEST_TIMEOUT = 45
#: "0" — веса выгружаются сразу после ответа (ADR-021, тот же принцип,
#: что у рефраза и GigaAM). Цена — холодный старт на каждом вопросе.
KEEP_ALIVE = "0"

#: Обрезка длинного фрагмента: пять чанков по 1200 символов уже
#: заметный промпт, а хвост протокола редко несёт ответ. Обрезаем по
#: символам, не по словам: показать модели меньше, чем есть, безопаснее,
#: чем показать больше, чем она удержит.
MAX_FRAGMENT_CHARS = 900

SYSTEM_PROMPT = (
    "Ты отвечаешь на вопрос пользователя строго по фрагментам его "
    "собственных документов. Не добавляй ничего от себя и не рассуждай "
    "о том, чего во фрагментах нет. Отвечай по-русски, коротко."
)

_CITATION_RE = re.compile(r"^[^\S\n]*ФРАГМЕНТЫ[^\S\n]*:[^\S\n]*([0-9][0-9,\s]*)[^\S\n]*$",
                          re.IGNORECASE | re.MULTILINE)
_NO_ANSWER_RE = re.compile(r"НЕТ\s+ОТВЕТА", re.IGNORECASE)


@dataclass(frozen=True)
class Synthesis:
    """Результат синтеза. `answered=False` — модель прочитала фрагменты
    и говорит, что ответа в них нет; это исход, а не ошибка."""

    answered: bool
    text: str = ""
    #: Номера фрагментов (1-based, как в промпте), на которые сослалась
    #: модель. Пусто при `answered=False`.
    used: tuple[int, ...] = ()


def build_prompt(question: str, fragments: list[str]) -> str:
    numbered = "\n\n".join(
        f"[{i}] {text.strip()[:MAX_FRAGMENT_CHARS]}"
        for i, text in enumerate(fragments, start=1)
    )
    return (
        f"Вопрос: {question}\n\n"
        f"Фрагменты из документов пользователя:\n{numbered}\n\n"
        "Ответь на вопрос, используя только эти фрагменты. "
        "Последней строкой напиши номера использованных фрагментов в виде\n"
        "ФРАГМЕНТЫ: 1,2\n"
        "Если ответа во фрагментах нет, напиши ровно одну строку: НЕТ ОТВЕТА"
    )


def parse_response(raw: str, *, fragment_count: int) -> Synthesis | None:
    """Разобрать ответ модели. `None` — формат не выдержан, доверять нечему."""
    text = (raw or "").strip()
    if not text:
        return None

    matches = list(_CITATION_RE.finditer(text))
    if matches:
        # Строк со ссылками бывает больше одной (модель повторяет формат
        # после каждого абзаца) — берутся все номера, и все такие строки
        # убираются из текста ответа: это разметка, а не ответ.
        used = tuple(sorted({
            n
            for match in matches
            for n in (int(part) for part in match.group(1).replace(" ", "").split(",") if part)
            if 1 <= n <= fragment_count
        }))
        body = _CITATION_RE.sub("", text).strip()
        if used and body:
            return Synthesis(answered=True, text=body, used=used)
        # Ссылки есть, а текста нет (или номера выдуманы) — это не ответ.
        return None

    if _NO_ANSWER_RE.search(text):
        return Synthesis(answered=False)
    # Текст без ссылок: проверить его нечем, показывать нельзя.
    return None


def synthesize_or_none(question: str, fragments: list[str]) -> Synthesis | None:
    """Fail-open обёртка для probe.py — см. докстринг модуля."""
    if not fragments:
        return None
    body = {
        "model": MODEL_NAME,
        "prompt": build_prompt(question, fragments),
        "system": SYSTEM_PROMPT,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            result = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("локальный синтез недоступен, откат на composer: %s", exc)
        return None
    return parse_response(result.get("response") or "", fragment_count=len(fragments))
