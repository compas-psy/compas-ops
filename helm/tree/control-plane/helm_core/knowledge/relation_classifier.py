"""R4.6.C2 (владелец 03.09.2026) — relation-or-NONE classifier.

Для ОДНОЙ уже заданной `RelationCandidate` (`relation_candidates.py`)
модель обязана вернуть либо `NONE`, либо один типизированный relation
из закрытого реестра §14.9. В отличие от R4.6.C (`semantic_extract_
twopass.py`, свободно перечислял пары между ВСЕМИ local_id и предлагал
произвольные связи — offline typed relation_precision 0.133, далеко от
gate) classifier здесь НЕ порождает пару сам: candidate.from_id/to_id
уже даны, промпт называет РОВНО эти два id и никаких других — модель
физически не может сослаться на третий объект, потому что о нём в
промпте ни слова.

Grounding УСИЛЕН относительно R4.5.3/R4.6.C (владелец п.5):
evidence_quote связи обязан быть дословной подстрокой
`candidate.evidence_context` — уже доказанно узкого контекста близости
пары (`relation_candidates.py`), а НЕ всего window_text. Модель не
может обосновать ложную связь случайной цитатой из другого места окна,
не имеющей отношения к этой конкретной паре.

`NONE` — явный default при недостаточном доказательстве, не крайний
случай: злоключение (нет relation в ответе, нет evidence_quote,
evidence не грaunded в контексте кандидата) trактуется так же, как
явный `NONE` — тихо, без ExtractionFailed на всё окно. Одна неудачная
классификация одной пары не имеет права обрушить всё окно (в отличие
от single/two-pass, где malformed JSON — provал всего вызова): при
десятках кандидатов в сложном окне это была бы слишком высокая цена
за одну шумную пару."""

from __future__ import annotations

import json
import logging

from . import semantic_extract
from .relation_candidates import RelationCandidate
from .semantic_extract import (
    ExtractedAtom, ExtractedEdge, ExtractedEntity, ExtractionFailed, SemanticRelationType,
)
from .semantic_extract_twopass import _RELATION_GLOSS

logger = logging.getLogger(__name__)

#: Ниже, чем MAX_REPAIR_ATTEMPTS (3) у single/two-pass: одна пара —
#: низкая ставка (при провале — NONE, не провал всего окна), а окно с
#: десятками кандидатов не может себе позволить по 3×120с ретраев на
#: КАЖДУЮ пару — тогда как single/two-pass ретраят весь вызов целиком
#: (высокая ставка, окно провалено полностью при исчерпании попыток).
C2_MAX_ATTEMPTS = 2

C2_SYSTEM_PROMPT = (
    "Ты — узкий классификатор связи между РОВНО ДВУМЯ объектами, уже "
    "найденными на предыдущем шаге. Тебе даны их id, описание и evidence, "
    "и фрагмент текста, доказывающий их близость.\n\n"
    "Правила, нарушать которые нельзя:\n"
    "- отвечай ТОЛЬКО про эту пару; не упоминай другие id, не создавай "
    "новые сущности или атомы — их не существует в этой задаче;\n"
    "- если фрагмент НЕ утверждает явную связь между этими двумя "
    "объектами — верни relation=\"NONE\"; NONE — умолчание при любом "
    "сомнении, не крайний случай;\n"
    "- если связь есть, выбери САМЫЙ ТОЧНЫЙ подходящий тип из списка "
    "ниже и заполни evidence_quote — дословную цитату из ФРАГМЕНТА "
    "НИЖЕ (не из общих знаний, не пересказ), которая доказывает именно "
    "эту связь;\n"
    "- related_to — последнее средство, когда связь явно есть, но ни "
    "один точный тип её не описывает, а не выбор по умолчанию.\n\n"
    "Разрешённые типы связи:\n"
    + "\n".join(f"- {name} — {gloss}" for name, gloss in _RELATION_GLOSS)
)

C2_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "relation": {"type": "string"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["relation"],
}


def _describe(object_id: str, obj: ExtractedEntity | ExtractedAtom) -> str:
    if isinstance(obj, ExtractedEntity):
        return f"{object_id} [{obj.entity_type}] {obj.label} — evidence: {obj.evidence_quote!r}"
    return f"{object_id} [{obj.kind}] {obj.title} — evidence: {obj.evidence_quote!r}"


def _c2_prompt(candidate: RelationCandidate, from_obj, to_obj, complaint: str | None) -> str:
    parts = [
        f"Объект A: {_describe(candidate.from_id, from_obj)}",
        f"Объект B: {_describe(candidate.to_id, to_obj)}",
    ]
    if complaint:
        parts.append(f"Прошлый ответ отклонён: {complaint}. Исправь и верни только объект.")
    parts.append(f"Фрагмент, доказывающий их близость:\n{candidate.evidence_context}")
    return "\n\n".join(parts)


def _parse_c2_response(raw: str) -> tuple[str, str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionFailed(f"C2: невалидный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractionFailed(f"C2: ожидался объект, пришёл {type(data).__name__}")
    relation = str(data.get("relation") or "").strip().lower()
    evidence_quote = str(data.get("evidence_quote") or "").strip()
    return relation, evidence_quote


def classify_relation(candidate: RelationCandidate, *, from_obj: ExtractedEntity | ExtractedAtom,
                      to_obj: ExtractedEntity | ExtractedAtom, model: str,
                      keep_alive: str | None, attempts: int = C2_MAX_ATTEMPTS,
                      ) -> tuple[ExtractedEdge | None, str | None]:
    """Классифицирует ОДНУ пару. Возвращает `(edge, None)` при явной
    типизированной связи, `(None, None)` при `NONE` (модель НЕ увидела
    связь — валидный, ожидаемый исход, не ошибка), `(None, reason)` при
    отбрасывании (не заземлено/не распарсилось после всех попыток) —
    `reason` для диагностики, тем же принципом, что `WindowExtraction.
    rejected` у single/two-pass."""
    complaint: str | None = None
    for attempt in range(1, attempts + 1):
        prompt = _c2_prompt(candidate, from_obj, to_obj, complaint)
        try:
            raw = semantic_extract._call_ollama(
                prompt, model=model, keep_alive=keep_alive,
                system=C2_SYSTEM_PROMPT, response_schema=C2_RESPONSE_SCHEMA)
            relation, evidence_quote = _parse_c2_response(raw)
            break
        except ExtractionFailed as exc:
            complaint = str(exc)
            logger.warning("C2 пара %s->%s не разобрана, попытка %d из %d: %s",
                           candidate.from_id, candidate.to_id, attempt, attempts, exc)
    else:
        return None, f"C2 пара {candidate.from_id}->{candidate.to_id}: не удалось разобрать за " \
                     f"{attempts} попыток: {complaint}"

    if not relation or relation == "none":
        return None, None

    if not evidence_quote:
        return None, f"C2 пара {candidate.from_id}->{candidate.to_id}: связь {relation!r} без evidence_quote"

    # Владелец п.5: evidence — подстрока КОНТЕКСТА КАНДИДАТА, не всего
    # окна. Тот же приём нормализации, что `_evidence_grounded()` в
    # semantic_extract.py, применённый к более узкому тексту.
    if not semantic_extract._evidence_grounded(evidence_quote, candidate.evidence_context):
        return None, (f"C2 пара {candidate.from_id}->{candidate.to_id}: evidence_quote "
                      f"{evidence_quote!r:.80} не найден в контексте кандидата")

    if relation not in semantic_extract._RELATION_TYPES:
        relation = SemanticRelationType.RELATED_TO.value

    return ExtractedEdge(from_local_id=candidate.from_id, relation_type=relation,
                         to_local_id=candidate.to_id, evidence_quote=evidence_quote), None
