"""Операции над найденным: что именно исполнитель должен сделать.

Распоряжение владельца 07.09.2026, п.3: «Сейчас наличие объекта
QuerySpec опережает возможности исполнителя. В общем пути тип операции
фактически не управляет ответом». Так и было: `intent` описывал, КАКОЙ
ответ считается ответом, но на исполнение не влиял — всё сводилось к
одному действию «покажи подходящий фрагмент».

Здесь заводится первая пара операций, которые он назвал первыми:

* «сколько» возвращает КОЛИЧЕСТВО, рассчитанное по найденному набору;
* «все» обходит полный подходящий набор либо прямо говорит о неполноте.

Обе детерминированные: ни одна не зовёт модель. Число, полученное
пересчётом, не может быть выдумано — а именно выдумка значений была
главным дефектом прошлых прогонов.

ЧТО ЭТО НЕ СЛОВАРЬ ТЕМ. Владелец запретил чинить разбор добавлением
слов вроде «беру» и «прилёт» — слов ПРЕДМЕТА. «Сколько» и «все» — слова
ОПЕРАЦИИ, и он сам определил их через требуемое поведение. Предмет
по-прежнему решает поиск, не регулярное выражение.

ЧЕГО ЗДЕСЬ НЕТ. Границы отдельных пунктов списка в ответ не выносятся:
разметки списков на приёме ещё нет (п.6), и восстанавливать её по
запятым значило бы показывать владельцу свою догадку как его данные.
Поэтому перечисление отдаёт исходное предложение дословно, а счёт —
число, полученное по разделителям в нём. Ограничение названное.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Значение, пункт, дата — то, что исполнялось и раньше: показать
#: подходящее. Умолчание, когда операция не названа.
OP_VALUE = "value"
OP_COUNT = "count"
OP_ENUMERATE = "enumerate"

_COUNT_RE = re.compile(r"\bсколько\b|\bколичество\b|\bкол-во\b", re.IGNORECASE)
_ENUMERATE_RE = re.compile(
    r"\bвсе\b|\bвсех\b|\bвсё\b|\bперечисли(?:те)?\b|\bсписок\b|\bкаждый\b",
    re.IGNORECASE)

#: Разделители перечисления. Точка с запятой и «и» перед последним
#: элементом — то же перечисление, что запятая.
_ITEM_SPLIT_RE = re.compile(r"\s*[,;]\s*|\s+и\s+", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
_STEM_LEN = 5
#: Перечислением считается предложение хотя бы с двумя разделителями:
#: одна запятая бывает в любой фразе, две подряд — уже список.
MIN_ITEMS = 3


def detect_operation(question: str) -> str:
    """Какую операцию просит вопрос. Счёт проверяется раньше
    перечисления: «сколько всего пунктов» — это счёт, хотя «всего» тоже
    слово перечисления."""
    if _COUNT_RE.search(question):
        return OP_COUNT
    if _ENUMERATE_RE.search(question):
        return OP_ENUMERATE
    return OP_VALUE


@dataclass(frozen=True)
class Enumeration:
    """Перечисление, найденное в тексте дословно."""

    sentence: str
    items: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.items)


def _stems(text: str) -> set[str]:
    return {word.lower()[:_STEM_LEN] for word in _WORD_RE.findall(text)}


def _items_of(sentence: str) -> tuple[str, ...]:
    """Пункты перечисления — или пусто, если границу списка не видно.

    ГРАНИЦА ВАЖНЕЕ РАЗДЕЛИТЕЛЕЙ. Первый замер этой функции дал «насчитал
    5» на «Запомнив дорожную аптечку, я кладу ибопрофен, лаперамид,
    пластырь и антисептик» — пунктов там четыре, а пятым посчиталась
    вводная часть предложения. Уверенное неправильное число — ровно тот
    дефект, который закрывался всю неделю, и повторять его в новом месте
    нельзя.

    Поэтому считаем только там, где начало списка ВИДНО:

    * есть двоеточие — список начинается после него;
    * двоеточия нет, но все элементы короткие (≤2 слов) — вводной части
      в предложении нет, оно всё и есть перечисление.

    Иначе — пусто: границу пришлось бы угадывать. Надёжно её даст
    разметка списков на приёме (п.6 распоряжения), и до неё «сколько»
    честно говорит, что посчитать не может.
    """
    body = sentence.rsplit(":", 1)[-1] if ":" in sentence else sentence
    parts = [part.strip(" .!?…") for part in _ITEM_SPLIT_RE.split(body)]
    items = tuple(part for part in parts if len(part) > 1)
    if len(items) < MIN_ITEMS:
        return ()
    if ":" in sentence:
        return items
    if all(len(_WORD_RE.findall(item)) <= 2 for item in items):
        return items
    return ()


def find_enumeration(question: str, text: str) -> Enumeration | None:
    """Предложение-перечисление, ближайшее к вопросу по словам.

    Ближайшее, а не первое: в лабораторном бланке перечислений много, и
    брать любое значило бы отвечать о чужой строке.
    """
    wanted = {stem for stem in _stems(question) if len(stem) >= 4}
    best: Enumeration | None = None
    best_score = -1
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        items = _items_of(sentence)
        if not items:
            continue
        # Ноль общих слов — не повод отказаться: фрагмент уже отобран
        # поиском по ЭТОМУ вопросу, и «сколько пунктов в аптечке» с
        # записью «Ибупрофен, лоперамид, пластырь, антисептик» общих
        # корней не имеет вовсе. Совпадение слов решает выбор МЕЖДУ
        # перечислениями, а не право ответить.
        score = len(_stems(sentence) & wanted)
        if score > best_score:
            best, best_score = Enumeration(sentence.strip(), items), score
    return best


@dataclass(frozen=True)
class OperationAnswer:
    text: str
    #: Номера фрагментов (1-based), по которым получен ответ.
    used: tuple[int, ...]


def run_count(question: str, fragments: list[str]) -> OperationAnswer | None:
    """«Сколько» — число, полученное пересчётом, а не пересказом.

    `None` — перечисления в найденном нет, считать нечего; вызывающий
    честно говорит об этом, а не выдаёт ближайшее число из текста.
    """
    for index, text in enumerate(fragments, start=1):
        found = find_enumeration(question, text)
        if found is not None:
            return OperationAnswer(
                text=f"Насчитал {found.count}. Дословно из записи: «{found.sentence}»",
                used=(index,))
    return None


def run_enumerate(question: str, fragments: list[str], *,
                  complete: bool) -> OperationAnswer | None:
    """«Все» — обход всего подходящего набора, а не первой находки.

    `complete=False` — набор кандидатов упёрся в лимит поиска, и о
    неполноте говорится прямо: «все» без этой оговорки было бы
    утверждением, которого у нас нет.
    """
    lines: list[str] = []
    used: list[int] = []
    for index, text in enumerate(fragments, start=1):
        found = find_enumeration(question, text)
        if found is not None:
            lines.append(f"«{found.sentence}»")
            used.append(index)
    if not lines:
        return None
    body = "\n".join(lines)
    if not complete:
        body += ("\n\nПоказал не всё: найденного больше, чем помещается в один "
                 "просмотр. Уточните вопрос, чтобы сузить.")
    return OperationAnswer(text=body, used=tuple(used))
