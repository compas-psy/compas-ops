"""R4.6.C2 (владелец 03.09.2026) — deterministic-only порождение пар-
кандидатов на связь, БЕЗ LLM и без права создавать факты.

R4.6.C (two-pass, `semantic_extract_twopass.py`) свободно перечислял
пары между ВСЕМИ local_id и предлагал произвольные связи — offline
пересчёт под typed-метрикой (R4.6.B.1) показал 0.133 typed relation_
precision, далеко от gate и лишь немного лучше single-pass (0.000).
R4.6.C2 меняет не промпт, а ПРОСТРАНСТВО РЕШЕНИЙ: пара-кандидат на
связь порождается здесь, детерминированно, по доказуемой близости
evidence в исходном тексте. `relation_classifier.py` затем только
классифицирует УЖЕ ДАННУЮ пару (NONE либо один типизированный тип из
закрытого реестра) — не может предложить пару сама и не видит других
объектов, кроме двух в конкретном кандидате.

Критерий близости (владелец, ни один не domain-specific):
  A. evidence spans пересекаются;
  B. label/alias одного объекта дословно встречается внутри evidence
     другого;
  C. оба spans — в одном предложении или одном абзаце;
  D. соседние предложения — только если между ними не более
     `ADJACENT_SENTENCE_PROXIMITY_CHARS` символов.

Точных character offsets в `ExtractedEntity`/`ExtractedAtom` нет —
`evidence_quote` намеренно остаётся текстовой цитатой, а не диапазоном
(`semantic_extract.validate()`: «provenance откладывается в R5»).
Поэтому spans здесь ищутся заново поиском подстроки `evidence_quote` в
нормализованном по пробелам окне — тот же приём, что и у
`_evidence_grounded()` в `semantic_extract.py`, не отдельная логика.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .semantic_extract import ExtractedAtom, ExtractedEntity

_WS = re.compile(r"\s+")
#: Грубый, доменно-нейтральный разделитель предложений: конец
#: предложения + пробел. Не лингвистический токенизатор — proximity
#: heuristic не обязана быть идеальной, только детерминированной и
#: одинаковой для всех доменов (владелец: «не domain-specific»).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
#: Разрыв абзаца в СЫРОМ тексте (до схлопывания пробелов) — искать
#: `\n\s*\n` уже ПОСЛЕ `_WS.sub(" ", ...)` бессмысленно: общая схлопка
#: пробелов стирает пустую строку раньше, чем до неё доходит очередь.
_RAW_PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n")
#: На нормализованном тексте (см. `_normalize_window`) разрыв абзаца —
#: уже РОВНО этот канонический разделитель, не общий `\s+`.
_NORMALIZED_PARAGRAPH_BREAK_RE = re.compile(r"\n\n")

#: Владелец: «для соседних предложений — только ограниченная proximity
#: window». Символы, не предложения — граница окна не зависит от длины
#: конкретного предложения.
ADJACENT_SENTENCE_PROXIMITY_CHARS = 200

#: Небольшой запас по краям контекста кандидата: связка между двумя
#: соседними предложениями («поэтому», «в результате») часто несёт
#: саму связь, но лежит МЕЖДУ спанами объектов, а не внутри одного из них.
_CONTEXT_PAD_CHARS = 40


def _normalize_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _normalize_window(text: str) -> str:
    """Схлопывает пробелы ВНУТРИ каждого абзаца отдельно, сохраняя сам
    разрыв абзаца как канонический `\\n\\n` — иначе `_WS.sub(" ", ...)`
    стёр бы пустую строку раньше, чем `_paragraph_index_at()` успеет её
    увидеть, и все объекты документа молча оказались бы в одном
    «абзаце»."""
    paragraphs = _RAW_PARAGRAPH_BREAK_RE.split(text)
    return "\n\n".join(_normalize_ws(p) for p in paragraphs)


@dataclass(frozen=True)
class _SpannedObject:
    object_id: str
    label: str
    aliases: tuple[str, ...]
    evidence_quote: str
    start: int
    end: int
    sentence_index: int
    paragraph_index: int


@dataclass(frozen=True)
class RelationCandidate:
    from_id: str
    to_id: str
    #: Контекст БЛИЗОСТИ пары — НЕ весь window_text, а минимальный
    #: диапазон, охватывающий оба spans (+ небольшой запас). Владелец
    #: п.5: classifier обязан искать evidence_quote связи ВНУТРИ этого
    #: контекста, не во всём окне — иначе модель может обосновать
    #: ложную связь случайной цитатой из другого места окна.
    evidence_context: str
    #: Диагностика (какой из критериев A-D сработал), не участвует в
    #: сопоставлении/оценке — только для логов и разбора кейсов.
    reason: str


def _split_sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0
    for piece in _SENTENCE_SPLIT_RE.split(text):
        if not piece:
            continue
        start = text.index(piece, pos)
        end = start + len(piece)
        spans.append((start, end))
        pos = end
    return spans or [(0, len(text))]


def _sentence_index_at(offset: int, sentence_spans: list[tuple[int, int]]) -> int:
    for i, (start, end) in enumerate(sentence_spans):
        if start <= offset < end:
            return i
    return len(sentence_spans) - 1


def _paragraph_index_at(offset: int, paragraph_starts: list[int]) -> int:
    idx = 0
    for start in paragraph_starts:
        if offset >= start:
            idx += 1
        else:
            break
    return idx


def _locate_objects(
    objects: list[tuple[str, str, tuple[str, ...], str]], window_norm: str,
) -> list[_SpannedObject]:
    sentence_spans = _split_sentence_spans(window_norm)
    paragraph_starts = [0] + [m.end() for m in _NORMALIZED_PARAGRAPH_BREAK_RE.finditer(window_norm)]

    located = []
    for object_id, label, aliases, evidence_quote in objects:
        quote = _normalize_ws(evidence_quote).strip()
        if not quote:
            continue
        start = window_norm.find(quote)
        if start < 0:
            # Не должно происходить — evidence_quote уже провалидирован
            # grounding-контролем R4.5.3 до того, как объект сюда попал.
            # Пропускаем молча: без span этот объект просто не участвует
            # ни в одном candidate, не крашим весь генератор.
            continue
        end = start + len(quote)
        located.append(_SpannedObject(
            object_id=object_id, label=label, aliases=aliases, evidence_quote=quote,
            start=start, end=end,
            sentence_index=_sentence_index_at(start, sentence_spans),
            paragraph_index=_paragraph_index_at(start, paragraph_starts)))
    return located


def _mentions(a: _SpannedObject, b: _SpannedObject) -> bool:
    a_names = (a.label, *a.aliases)
    b_names = (b.label, *b.aliases)
    return (any(_normalize_ws(n).casefold() in b.evidence_quote.casefold() for n in a_names if n)
            or any(_normalize_ws(n).casefold() in a.evidence_quote.casefold() for n in b_names if n))


def _proximity_reason(a: _SpannedObject, b: _SpannedObject) -> str | None:
    if a.start < b.end and b.start < a.end:
        return "overlap"
    if _mentions(a, b):
        return "mention"
    if a.sentence_index == b.sentence_index:
        return "same_sentence"
    if a.paragraph_index == b.paragraph_index:
        return "same_paragraph"
    if abs(a.sentence_index - b.sentence_index) == 1:
        gap = (b.start - a.end) if a.start < b.start else (a.start - b.end)
        if gap <= ADJACENT_SENTENCE_PROXIMITY_CHARS:
            return "adjacent_sentence"
    return None


def generate_candidates(entities: list[ExtractedEntity], atoms: list[ExtractedAtom],
                        window_text: str) -> list[RelationCandidate]:
    """Порождает пары-кандидаты на связь между УЖЕ извлечёнными
    entities/atoms — не создаёт новых объектов, не вызывает LLM.

    `from_id`/`to_id` — детерминированный порядок по позиции spans в
    тексте (кто раньше начинается — тот `from`); при равном старте —
    по `object_id` для стабильности. Доменно-нейтрально: генератор не
    знает семантики связи, только её текстовую близость."""
    window_norm = _normalize_window(window_text)
    objects = (
        [(e.local_id, e.label, e.aliases, e.evidence_quote) for e in entities]
        + [(a.local_id, a.title, (), a.evidence_quote) for a in atoms]
    )
    located = _locate_objects(objects, window_norm)

    candidates: list[RelationCandidate] = []
    for i, a in enumerate(located):
        for b in located[i + 1:]:
            reason = _proximity_reason(a, b)
            if reason is None:
                continue
            first, second = sorted((a, b), key=lambda o: (o.start, o.object_id))
            ctx_start = max(0, min(first.start, second.start) - _CONTEXT_PAD_CHARS)
            ctx_end = min(len(window_norm), max(first.end, second.end) + _CONTEXT_PAD_CHARS)
            candidates.append(RelationCandidate(
                from_id=first.object_id, to_id=second.object_id,
                evidence_context=window_norm[ctx_start:ctx_end], reason=reason))
    return candidates
