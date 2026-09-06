"""Контекстные якоря даты: где в тексте стоит дата и чего она касается.

Задача, поставленная владельцем 06.09.2026. Замер на полном корпусе:
539 датируемых узлов, у 21 есть `occurred_at`. Контракт извлечения
починен, grounding работает, а дат нет — потому что в медицинском
документе дата стоит В ШАПКЕ таблицы или бланка, а значение в строке.
Требование «дата обязана быть в той же цитате, что и факт» эту связь
теряет; разрешение «взять любую дату из документа» её выдумывает.

ПОЧЕМУ НЕ `document_date → occurred_at`. Прямое указание владельца:
это новый тип тихой выдумки. Дата печати бланка, дата рождения, дата
взятия материала и нормативный референс — четыре разные вещи, и
присвоить их все каждому атому значит получить граф, уверенно
отвечающий неправду.

ЧТО ЗДЕСЬ ЕСТЬ. Только первая ступень: детерминированно найти в тексте
даты, определить ПО СОСЕДНЕЙ ПОДПИСИ их роль и вернуть каждую с точным
спаном. Никакой модели, никаких догадок: не распознали подпись — роль
`unlabelled`, и наследовать от такой нельзя.

ЧЕГО ЗДЕСЬ НЕТ. Самого наследования. Правило «атом без своей даты
берёт дату якоря» имеет смысл писать после замера: сколько окон вообще
содержат ровно один однозначный якорь. Если таких окон мало, правило
не окупится, и честнее это узнать до реализации, а не после.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Роль якоря. Наследовать дату атому можно будет ТОЛЬКО от `EVENT`:
#: остальные три описывают не то, когда случилось событие.
ROLE_EVENT = "event"           # дата приёма, осмотра, исследования, забора
ROLE_DOCUMENT = "document"     # дата выдачи, печати, формирования бланка
ROLE_REFERENCE = "reference"   # дата рождения, нормативный референс
ROLE_UNLABELLED = "unlabelled"  # дата без распознанной подписи

#: Месяцы: именительный и родительный. В тексте встречается и
#: «август 2026», и «26 августа 2026».
_MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS_RU, key=len, reverse=True))

#: Три формы записи. Голый год НЕ ловится намеренно: «2024» в
#: медицинском бланке чаще номер, код или часть диапазона нормы, чем
#: дата, и якорь из него был бы шумом, от которого правило наследования
#: пришлось бы защищать отдельно.
_NUMERIC_RE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_ALT})\w*\s+(\d{{4}})\b", re.IGNORECASE)
_MONTH_YEAR_RE = re.compile(rf"\b({_MONTH_ALT})\w*\s+(\d{{4}})\b", re.IGNORECASE)

#: Подписи. Порядок важен: сначала более узкие. Проверяется ХВОСТ
#: текста перед датой — до 48 символов, потому что подпись стоит рядом
#: («Дата приёма: 12.03.2025», «Осмотр от 12.03.2025»), а не абзацем выше.
_LABEL_WINDOW = 48
_LABELS: tuple[tuple[str, str], ...] = (
    (ROLE_REFERENCE, r"дата\s+рождения|д\.\s?р\.|год\s+рождения"),
    (ROLE_EVENT,
     r"дата\s+(приёма|приема|осмотра|исследовани\w*|обследовани\w*|"
     r"взяти\w*|забора|обращени\w*|консультаци\w*|госпитализаци\w*|операци\w*)|"
     r"(приём|прием|осмотр|исследование|обследование|консультация|"
     r"анализ|заключение)\s+от|"
     r"дата\s+и\s+время\s+(приёма|приема|осмотра)|"
     r"принят\w*|выполнен\w*|проведен\w*"),
    (ROLE_DOCUMENT,
     r"дата\s+(выдачи|печати|формировани\w*|документа|протокола|"
     r"регистрации|создани\w*)|"
     r"выдан\w*|напечатан\w*|сформирован\w*|зарегистрирован\w*"),
)
_LABEL_RES = tuple((role, re.compile(pattern, re.IGNORECASE)) for role, pattern in _LABELS)

#: Та же эвристика, что в `semantic_extract._RELATIVE_DATE_MARKERS_RE`.
#: Здесь она нужна для другого: если рядом с датой стоит «в прошлом
#: году», дата в тексте описывает не эту дату, а сдвиг от неё.
_RELATIVE_NEAR_RE = re.compile(
    r"\bв прошл\w+|\bна прошл\w+|\bв следующ\w+|\bна следующ\w+|\bгод назад\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class DateAnchor:
    """Одна дата в тексте: что, какой точности, о чём и где именно."""

    value: str        # ISO: YYYY-MM-DD | YYYY-MM
    precision: str    # day | month
    role: str
    char_start: int
    char_end: int
    quote: str

    def as_dict(self) -> dict:
        return {"value": self.value, "precision": self.precision, "role": self.role,
                "char_start": self.char_start, "char_end": self.char_end}


def _role_for(text: str, start: int) -> str:
    """Роль по подписи слева от даты. Не нашли — `unlabelled`."""
    head = text[max(0, start - _LABEL_WINDOW):start]
    if _RELATIVE_NEAR_RE.search(head):
        return ROLE_UNLABELLED
    for role, pattern in _LABEL_RES:
        if pattern.search(head):
            return role
    return ROLE_UNLABELLED


def _valid(year: int, month: int, day: int | None) -> bool:
    """Отсев заведомо не-дат: номеров, кодов, диапазонов норм."""
    if not 1900 <= year <= 2100 or not 1 <= month <= 12:
        return False
    return day is None or 1 <= day <= 31


def _add(found: dict[tuple[int, int], DateAnchor], anchor: DateAnchor) -> None:
    """Один спан — один якорь. Более длинное совпадение выигрывает:
    «26 августа 2026» не должно распасться на «август 2026»."""
    for (start, end) in list(found):
        if start <= anchor.char_start and anchor.char_end <= end:
            return  # уже покрыт более длинным
        if anchor.char_start <= start and end <= anchor.char_end:
            del found[(start, end)]
    found[(anchor.char_start, anchor.char_end)] = anchor


def find_date_anchors(text: str) -> list[DateAnchor]:
    """Все даты текста с ролями и спанами, по возрастанию позиции."""
    found: dict[tuple[int, int], DateAnchor] = {}

    for match in _ISO_RE.finditer(text):
        year, month, day = (int(g) for g in match.groups())
        if _valid(year, month, day):
            _add(found, DateAnchor(f"{year:04d}-{month:02d}-{day:02d}", "day",
                                   _role_for(text, match.start()),
                                   match.start(), match.end(), match.group(0)))

    for match in _NUMERIC_RE.finditer(text):
        day, month, year = (int(g) for g in match.groups())
        if _valid(year, month, day):
            _add(found, DateAnchor(f"{year:04d}-{month:02d}-{day:02d}", "day",
                                   _role_for(text, match.start()),
                                   match.start(), match.end(), match.group(0)))

    for match in _DAY_MONTH_RE.finditer(text):
        day = int(match.group(1))
        month = _MONTHS_RU[match.group(2).lower()]
        year = int(match.group(3))
        if _valid(year, month, day):
            _add(found, DateAnchor(f"{year:04d}-{month:02d}-{day:02d}", "day",
                                   _role_for(text, match.start()),
                                   match.start(), match.end(), match.group(0)))

    for match in _MONTH_YEAR_RE.finditer(text):
        month = _MONTHS_RU[match.group(1).lower()]
        year = int(match.group(2))
        if _valid(year, month, None):
            _add(found, DateAnchor(f"{year:04d}-{month:02d}", "month",
                                   _role_for(text, match.start()),
                                   match.start(), match.end(), match.group(0)))

    return [found[key] for key in sorted(found)]


def inheritable_anchor(anchors: list[DateAnchor]) -> DateAnchor | None:
    """Якорь, от которого МОЖНО унаследовать дату, — или ничего.

    Условие ровно одно и намеренно жёсткое: среди якорей окна есть
    РОВНО ОДИН с ролью `event`. Два события в окне — неизвестно, к
    какому относится факт; ноль — наследовать не от чего; дата
    документа, рождения и референса не годятся по определению.

    Всё остальное (сам перенос даты в атом, пометка её источника,
    запрет перезаписывать дату, добытую из собственной цитаты) —
    вторая ступень, и она пишется после замера, а не до.
    """
    events = [anchor for anchor in anchors if anchor.role == ROLE_EVENT]
    return events[0] if len(events) == 1 else None
