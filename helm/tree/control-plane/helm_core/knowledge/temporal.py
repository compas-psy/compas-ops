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
from dataclasses import dataclass, replace
from datetime import date

#: Роль якоря. Наследовать дату атому можно будет ТОЛЬКО от `EVENT`:
#: остальные четыре описывают не то, когда случилось событие.
ROLE_EVENT = "event"           # дата приёма, осмотра, исследования, забора
ROLE_PLANNED = "planned"       # «повторная явка НА 12.03.2025» — будущее
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

#: Словарь СНЯТ С КОРПУСА (date-label-recon.sh, прогон 356), а не
#: придуман. Первая его версия узнавала 87 якорей из 273: я перечислил
#: формы, в которых медицинский бланк выглядит в моём представлении.
#: Числа в комментариях — сколько якорей корпуса дала каждая группа.
_LABELS: tuple[tuple[str, str], ...] = (
    (ROLE_REFERENCE,
     # Дата рождения. Самая дорогая ошибка при наследовании.
     r"дата\s+рождения|д\.\s?р\.|год\s+рождения|"
     # Нормативная ссылка: «Приказ Минздрава России №123н ОТ …» (7).
     # Обязана стоять ВЫШЕ события: иначе её съест правило «… от <дата>».
     r"(приказ\w*|постановлени\w*|минздрав\w*|санпин|гост|"
     r"федеральн\w+\s+закон)[^.]{0,40}\bот\s*[:\-]?\s*$"),
    (ROLE_PLANNED,
     # «Повторная явка НА 12.03.2025» (24) — вторая по величине группа
     # корпуса и дата В БУДУЩЕМ. Отличает её предлог: «от» — то, что
     # произошло, «на» — то, что назначено. Без отдельной роли эту
     # форму рано или поздно припишут к событиям по созвучию («явка»
     # почти «приём»), и факты уедут вперёд во времени.
     r"(явка|явиться|назначен\w*|запис\w+|приём|прием|осмотр|"
     r"контроль|госпитализаци\w*)[^.]{0,24}\bна\s*[:\-]?\s*$"),
    (ROLE_EVENT,
     r"дата\s+(приёма|приема|осмотра|исследовани\w*|обследовани\w*|"
     r"взяти\w*|забора|поступлени\w*|обращени\w*|консультаци\w*|"
     r"госпитализаци\w*|операци\w*)|"
     r"дата\s+и\s+время\s+(приёма|приема|осмотра)|"
     # «… ОТ <дата>»: предлог прошедшего. Модальности взяты из корпуса —
     # «норме ЭГДС от» (2), «УЗИ ОБП от» (2); прежний список
     # ограничивался словом «исследование» и их не ловил.
     r"(приём|прием|осмотр|исследовани\w*|обследовани\w*|консультаци\w*|"
     r"анализ\w*|заключени\w*|эгдс|фгдс|фкс|узи|кт|мрт|экг|эхокг|ээг|"
     r"рентген\w*|флюорографи\w*|колоноскопи\w*|гастроскопи\w*|"
     r"биопси\w*|операци\w*|госпитализаци\w*)[^.]{0,24}\bот\s*[:\-]?\s*$|"
     # Заголовок раздела с датой: «Биохимические исследования <дата>»,
     # «Патолого-анатомического исследования <дата>» (6).
     r"исследовани\w*\s*[:\-]?\s*$|"
     # «Биопсийного операционного материала <дата>» (4) — дата забора.
     r"материала\s*[:\-]?\s*$|"
     r"принят\w*|выполнен\w*|проведен\w*"),
    (ROLE_DOCUMENT,
     r"дата\s+(выдачи|печати|формировани\w*|документа|протокола|"
     r"регистрации|создани\w*|направлени\w*)|"
     r"выдан\w*|напечатан\w*|сформирован\w*|зарегистрирован\w*|"
     # Голое «Дата» в шапке бланка: «Направление №123 Дата <дата>»,
     # «ЭМК №… Дата <дата>» — 79 якорей, 42% всех неузнанных.
     # ЭТО ДАТА ДОКУМЕНТА, А НЕ ПРИЁМА. Соблазн считать её датой
     # события велик — в направлении она обычно и есть день визита, —
     # но это ровно та подстановка, которую владелец запретил
     # 06.09.2026: «дата печати бланка, дата рождения, дата взятия
     # материала и норматив — четыре разные вещи». Проверяется
     # ПОСЛЕДНЕЙ, чтобы «дата приёма» успела уйти в событие.
     r"дата\s*[:\-—№]?\s*$"),
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


def _sentence_cut(head: str) -> int:
    """Позиция конца последнего предложения в тексте перед датой, или -1.

    Точка сокращения концом предложения НЕ считается: «д.р. 04.07.1985»
    — одна подпись, а не две фразы. Признак сокращения простой и
    проверяемый: слово перед точкой короче двух символов или само
    содержит точку («д.р», «г», «им»).
    """
    best = -1
    for separator in (". ", "; ", "! ", "? "):
        index = head.rfind(separator)
        while index > best:
            word = head[:index].rsplit(" ", 1)[-1]
            if separator != ". " or (len(word) > 1 and "." not in word):
                best = index
                break
            index = head.rfind(separator, 0, index)
    return best


def _head_for(text: str, start: int, previous_end: int) -> str:
    """Текст слева от даты, в котором можно искать её подпись.

    Две границы, и обе поставлены по ошибкам, найденным владельцем
    06.09.2026 на «Дата рождения: 04.07.1985. Приём от 12.03.2025»,
    где обе даты получали роль `reference`:

    * ЧУЖАЯ ДАТА. Окно не переходит конец предыдущей даты: подпись,
      стоящая перед ней, относится к ней, а не к следующей.
    * КОНЕЦ ПРЕДЛОЖЕНИЯ. Часть шаблонов («дата рождения», «выдан»)
      не привязана к концу окна и потому срабатывала через точку.
      Перенос строки границей НЕ считается: в бланке подпись сплошь и
      рядом стоит строкой выше своего значения.
    """
    head = text[max(previous_end, start - _LABEL_WINDOW):start]
    return head[_sentence_cut(head) + 1:]


#: Подпись СПРАВА от даты: «12.03.2025 выполнено УЗИ». Найдено
#: владельцем 06.09.2026 — распознаватель смотрел только влево и такую
#: форму не видел вовсе. Список глаголов тот же, что в событии слева:
#: роль от стороны не меняется, меняется только место подписи.
_TAIL_WINDOW = 32
_TAIL_LABELS: tuple[tuple[str, str], ...] = (
    (ROLE_EVENT,
     r"^\s*[-—:]?\s*(выполнен\w*|проведен\w*|сделан\w*|принят\w*|"
     r"осмотрен\w*|обследован\w*|госпитализирован\w*|прооперирован\w*|"
     r"состоял\w*|проходил\w*)"),
)
_TAIL_RES = tuple((role, re.compile(pattern, re.IGNORECASE)) for role, pattern in _TAIL_LABELS)


def _role_for(text: str, start: int, end: int = 0, previous_end: int = 0,
              next_start: int | None = None) -> str:
    """Роль по подписи: сначала слева, потом справа. Нет — `unlabelled`.

    Слева приоритетнее: явная подпись бланка («Дата приёма:») сильнее
    глагола, случайно оказавшегося после числа.
    """
    head = _head_for(text, start, previous_end)
    if _RELATIVE_NEAR_RE.search(head):
        return ROLE_UNLABELLED
    for role, pattern in _LABEL_RES:
        if pattern.search(head):
            return role

    limit = min(len(text), end + _TAIL_WINDOW)
    if next_start is not None:
        limit = min(limit, next_start)
    tail = text[end:limit]
    for role, pattern in _TAIL_RES:
        if pattern.search(tail):
            return role
    return ROLE_UNLABELLED


def _valid(year: int, month: int, day: int | None) -> bool:
    """Отсев заведомо не-дат: номеров, кодов, диапазонов норм.

    День проверяется КАЛЕНДАРЁМ, а не потолком 31. Найдено владельцем
    06.09.2026: «Приём от 31.02.2025» принималось как дата. Такой
    «даты» не существует, и в тексте это либо опечатка, либо не дата
    вовсе — в обоих случаях якорем ей быть нельзя. Тот же дефект
    пропускал 29.02 в невисокосный год.
    """
    if not 1900 <= year <= 2100 or not 1 <= month <= 12:
        return False
    if day is None:
        return True
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


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
                                   ROLE_UNLABELLED,
                                   match.start(), match.end(), match.group(0)))

    for match in _NUMERIC_RE.finditer(text):
        day, month, year = (int(g) for g in match.groups())
        if _valid(year, month, day):
            _add(found, DateAnchor(f"{year:04d}-{month:02d}-{day:02d}", "day",
                                   ROLE_UNLABELLED,
                                   match.start(), match.end(), match.group(0)))

    for match in _DAY_MONTH_RE.finditer(text):
        day = int(match.group(1))
        month = _MONTHS_RU[match.group(2).lower()]
        year = int(match.group(3))
        if _valid(year, month, day):
            _add(found, DateAnchor(f"{year:04d}-{month:02d}-{day:02d}", "day",
                                   ROLE_UNLABELLED,
                                   match.start(), match.end(), match.group(0)))

    for match in _MONTH_YEAR_RE.finditer(text):
        month = _MONTHS_RU[match.group(1).lower()]
        year = int(match.group(2))
        if _valid(year, month, None):
            _add(found, DateAnchor(f"{year:04d}-{month:02d}", "month",
                                   ROLE_UNLABELLED,
                                   match.start(), match.end(), match.group(0)))

    # Роли назначаются последним проходом, когда известны ВСЕ даты
    # текста: подпись ищется в окне, не переходящем соседнюю дату ни
    # влево, ни вправо. До 06.09.2026 роль вычислялась прямо в разборе,
    # каждая дата в одиночку, — отсюда и «обе даты reference».
    anchors = [found[key] for key in sorted(found)]
    roled: list[DateAnchor] = []
    for index, anchor in enumerate(anchors):
        previous_end = anchors[index - 1].char_end if index else 0
        next_start = anchors[index + 1].char_start if index + 1 < len(anchors) else None
        roled.append(replace(anchor, role=_role_for(
            text, anchor.char_start, anchor.char_end, previous_end, next_start)))
    return roled


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


def content_date(text: str) -> date | None:
    """Дата САМОГО документа: когда он составлен.

    Не «дата любого факта внутри» и не дата загрузки. Порядок ровно
    такой: сначала подписанная дата документа («Дата: 25.08.2026» в
    шапке бланка), затем единственная дата события, если документа нет.
    Несколько событий — отказ: выбирать между ними было бы догадкой.

    Нужна для двух вещей, которых без неё сделать нельзя: ответить на
    «в последний раз» и сказать в ответе, К КАКОМУ ЧИСЛУ относится
    значение. Найдено живым ответом владельцу 07.09.2026: на вопрос о
    последнем анализе система не знала дат своих документов вовсе.
    """
    anchors = find_date_anchors(text)
    documents = [a for a in anchors if a.role == ROLE_DOCUMENT and a.precision == "day"]
    if documents:
        return date.fromisoformat(documents[0].value)
    events = [a for a in anchors if a.role == ROLE_EVENT and a.precision == "day"]
    if len(events) == 1:
        return date.fromisoformat(events[0].value)
    return None
