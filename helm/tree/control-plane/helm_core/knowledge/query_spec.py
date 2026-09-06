"""Что именно спросили: разбор вопроса до того, как его исполнять.

Заведено 06.09.2026 по распоряжению владельца: «Реализуй общий
QuerySpec/executor с проверкой доступа, времени, источников и полноты
результата. Не создавай отдельный обработчик под каждый пример».

ЧТО БЫЛО ДО. Вопрос шёл в `probe()` как строка, и каждое его свойство
выяснялось по дороге в разных местах: тенант — в начале, год — внутри
исполнителя врачей, область данных — перед решением об оплате,
неприменимый период — в форматтере ответа. Ни одно из этих свойств не
существовало как факт, о котором можно спросить, и потому ответ не мог
честно перечислить, что он применил, а что нет.

ЧТО ЗДЕСЬ. Один объект, собираемый детерминированно из вопроса и
тенанта, со всеми условиями отбора в явном виде. Никакой модели: это
разбор формы вопроса, а не понимание смысла. Понимание — работа
исполнителя и ступени синтеза, и они получают уже разобранный запрос, а
не строку.

ПОЧЕМУ УТОЧНЕНИЕ — ЧАСТЬ РАЗБОРА, А НЕ ОШИБКА. «Что там прописал врач?»
не имеет ответа сам по себе: «там» указывает на документ из разговора,
а разговора у `probe()` нет (§6 CHUNKING_AND_BAD_ANSWERS: «у диалога
нет памяти»). До сих пор система на такой вопрос отвечала ближайшим
похожим текстом — то есть угадывала, о чём речь. Правильный ответ —
спросить. Правило общее: указательное слово без антецедента даёт
уточнение, а не догадку, независимо от темы вопроса.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date

from .query_scope import _RECORD_WORD_RE, is_personal_data_question

#: Что вопрос просит сделать. Не тема и не домен: домен решает, ГДЕ
#: искать, а намерение — КАКОЙ ответ считается ответом.
INTENT_ENUMERATE = "enumerate"    # «каких врачей», «какие места работы»
INTENT_LOOKUP = "lookup"          # «какое было давление», «где я работал»
INTENT_DECISION = "decision"      # «что решили по подзадачам»
INTENT_UNKNOWN = "unknown"

_ENUMERATE_RE = re.compile(
    r"\bкак(?:их|ие|им)\b|\bперечисл\w*|\bсписок\b|\bвсе\s+\w+\s+котор",
    re.IGNORECASE)
_DECISION_RE = re.compile(
    r"\bреш(?:ени\w*|или|ено|ил)\b|\bдоговорил\w*|\bутвержд\w*|"
    r"\bвыбрал\w*|\bитог\w*\s+обсужд",
    re.IGNORECASE)
_LOOKUP_RE = re.compile(
    # «какое У МЕНЯ было давление» — между вопросительным словом и
    # глаголом стоят ещё слова, и первая версия их не пропускала.
    r"\bкак(?:ое|ой|ая|ие)\b[^.?!]{0,30}?\b(был|были|было)\b|"
    r"\bкогда\b|\bгде\b|\bсколько\b|\bкто\b|"
    r"\bчто\s+(показал|написал|прописал|назначил|обнаружил)",
    re.IGNORECASE)

#: Указательные слова, у которых антецедент лежит вне вопроса.
#: «Что ТАМ прописал врач» — в каком «там»? Разговора у probe нет.
_DEICTIC_RE = re.compile(
    r"\bчто\s+там\b|\bтам\s+же\b|\bв\s+нём\b|\bв\s+этом\s+документе\b|"
    r"\bв\s+этом\s+файле\b|\bпо\s+нему\b|\bоттуда\b",
    re.IGNORECASE)

#: Год: явные четыре цифры либо «в этом году». Остальные формы периода
#: исполнитель применить не умеет — они попадают в `unsupported`.
_EXPLICIT_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_THIS_YEAR_RE = re.compile(r"в\s+этом\s+году", re.IGNORECASE)


@dataclass(frozen=True)
class TimeConstraint:
    """Временное условие вопроса и честный ответ, применимо ли оно.

    Два поля, а не одно, потому что «периода нет» и «период есть, но я
    его не умею» — разные вещи, и ответ обязан их различать. До
    06.09.2026 второе молча превращалось в первое.
    """

    year: int | None = None
    #: Текст ограничения, которое применить нечем («в марте», «за
    #: последний год»). Не `None` — ответ обязан назвать его вслух.
    unsupported: str | None = None

    @property
    def present(self) -> bool:
        return self.year is not None or self.unsupported is not None


@dataclass(frozen=True)
class QuerySpec:
    """Разобранный запрос: всё, чем ограничен ответ, в явном виде."""

    question: str
    #: Доступ. Не «по умолчанию владелец»: тенант входит в запрос, и
    #: исполнитель не имеет права искать, не получив его (v3.8 §14.4).
    tenant_id: uuid.UUID
    intent: str
    time: TimeConstraint
    #: Вопрос о данных владельца. Решает право уйти в платную модель.
    personal: bool
    #: Чего не хватает, чтобы вопрос вообще имел ответ. Не `None` —
    #: исполнять нечего, надо спрашивать.
    clarification: str | None = None
    #: Домены, куда смотреть. Пусто — везде, куда пускает тенант.
    domains: tuple[str, ...] = field(default_factory=tuple)


def detect_intent(question: str) -> str:
    """Какой ответ считается ответом на этот вопрос.

    Порядок проверок не случаен: «какие решения приняли» — и
    перечисление, и решение; побеждает решение, потому что список
    решений без самих решений ответом не будет.
    """
    if _DECISION_RE.search(question):
        return INTENT_DECISION
    if _ENUMERATE_RE.search(question):
        return INTENT_ENUMERATE
    if _LOOKUP_RE.search(question):
        return INTENT_LOOKUP
    return INTENT_UNKNOWN


def parse_time(question: str, *, today: date | None = None) -> TimeConstraint:
    """Год, который умеем применить, и период, который не умеем."""
    from .query_router import unsupported_period  # локально: цикл импортов

    year = None
    match = _EXPLICIT_YEAR_RE.search(question)
    if match:
        year = int(match.group(1))
    elif _THIS_YEAR_RE.search(question):
        year = (today or date.today()).year
    return TimeConstraint(year=year, unsupported=unsupported_period(question))


def build_query_spec(question: str, *, tenant_id: uuid.UUID,
                     has_dialogue_context: bool = False,
                     today: date | None = None) -> QuerySpec:
    """Разобрать вопрос. Ничего не ищет и в базу не ходит.

    `has_dialogue_context` — знает ли вызывающий, о каком документе шла
    речь. Сегодня всегда `False`: памяти диалога в системе нет. Параметр
    существует, чтобы отсутствие этой памяти было видно в сигнатуре, а
    не подразумевалось: когда память появится, менять придётся вызов, а
    не правило.
    """
    clarification = None
    if _DEICTIC_RE.search(question) and not has_dialogue_context:
        clarification = (
            "Уточните, о каком документе речь: в вопросе есть «там», а "
            "прошлые сообщения я не помню.")

    intent = detect_intent(question)

    # Вопрос о СВОЁМ документе бывает без единого притяжательного слова:
    # «что решили по подзадачам в ТЗ» — ни «мой», ни «я», а платная
    # модель этого ТЗ всё равно не знает. Форма вопроса одна этого не
    # различает; различает связка «спрашивают о факте» + «речь о
    # записи». Определение («что такое ферритин») под неё не подходит:
    # у него нет ни того, ни другого.
    asks_about_a_record = (intent in (INTENT_LOOKUP, INTENT_DECISION, INTENT_ENUMERATE)
                           and bool(_RECORD_WORD_RE.search(question)))
    personal = is_personal_data_question(question) or asks_about_a_record

    return QuerySpec(
        question=question,
        tenant_id=tenant_id,
        intent=intent,
        time=parse_time(question, today=today),
        personal=personal,
        clarification=clarification,
    )
