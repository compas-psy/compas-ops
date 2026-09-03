"""R4.6.E (владелец 03.09.2026) — relation existence/typing redesign.

R4.6.C2 (единый relation-or-NONE classifier, одна LLM-задача на пару)
провалился поперёк ЧЕТЫРЁХ независимых моделей (`qwen2.5:7b`,
`llama3.2:3b`, `phi3.5`, `mistral:7b`: typed precision 0.103/0.000/
0.043/0.053) — владелец диагностировал это не как предел локальных
3-7B моделей, а как ЧЕТЫРЕ структурных дефекта самой постановки задачи:

1. forced-choice bias: старый промпт называл пару «фрагмент,
   доказывающий их близость» и сразу показывал реестр из 15 типов —
   генеративная модель охотно «выбирает хоть что-то», вместо того
   чтобы всерьёз спросить себя «а есть ли тут вообще связь»;
2. `relation` был произвольной строкой, не `enum` — схема не мешала
   модели вернуть что угодно;
3. неизвестный тип МОЛЧА нормализовался (`coercion`) в `related_to` —
   то же правило §14.9, что и у single/two-pass, но там оно смягчает
   шум свободной генерации (модель никогда не видела закрытого списка),
   а здесь модель уже видела реестр и всё равно не выбрала из него ни
   одного значения — это провал структурированного вывода, не
   орфографический вариант, и не должен молча превращаться в ребро;
4. направление (`from`/`to`) решал ПОРЯДОК ПОЯВЛЕНИЯ объектов в тексте
   (`relation_candidates.py`: кто раньше начинается — тот `from`), а не
   модель — семантическое направление связи не обязано совпадать с
   порядком слов, и classifier не мог сказать «связь есть, но B→A».

Редизайн разделяет одну задачу на две независимых LLM-вызова:

PASS 2A — EXISTENCE ONLY (`_existence_prompt`/`EXISTENCE_*`). Модель
видит ДВА объекта и evidence, отвечает `entailed: true|false` — реестр
типов связи в этом промпте ОТСУТСТВУЕТ, поэтому у forced-choice bias из
п.1 физически нет опоры: модели нечего «выбрать хоть что-то из списка»,
кроме да/нет. `entailed=false` — явный default при любом сомнении, тот
же принцип, что раньше был у `NONE`, не крайний случай.

PASS 2B — TYPING + DIRECTION (`_typing_prompt`/`TYPING_*`), запускается
ТОЛЬКО если 2A вернул `entailed=true`. Реестр типов появляется здесь и
только здесь, СХЕМОЙ (`enum`, п.2 устранён) — Ollama `format` со схемой
уже гарантирует форму ответа (см. `semantic_extract.RESPONSE_SCHEMA`).
Тип вне реестра (если модель всё же его вернула в обход схемы) — REJECT,
НИКОГДА coercion в `related_to` (п.3 устранён). Направление —
`direction: a_to_b|b_to_a`, часть ответа модели, а не порядок появления
объектов в тексте (п.4 устранён).

`evidence_quote` проверяется ОДИН раз, в 2A, против `candidate.
evidence_context` (владелец п.5 прежнего мандата — тот же приём, что и
раньше, не ослаблен); 2B получает уже доказанное свидетельство, не
просит новое — «PASS 2B input: A, B, proven evidence» (владелец)."""

from __future__ import annotations

import json
import logging

from . import semantic_extract
from .relation_candidates import RelationCandidate
from .semantic_extract import ExtractedAtom, ExtractedEdge, ExtractedEntity, ExtractionFailed
from .semantic_extract_twopass import _RELATION_GLOSS

logger = logging.getLogger(__name__)

#: Общий потолок попыток на КАЖДЫЙ из двух вызовов (2A, отдельно 2B) —
#: та же логика, что была у прежнего C2_MAX_ATTEMPTS: одна пара — низкая
#: ставка (при провале — NONE/reject, не провал всего окна), окно с
#: десятками кандидатов не может себе позволить по 3 ретрая на КАЖДЫЙ
#: вызов КАЖДОЙ пары.
MAX_ATTEMPTS = 2

EXISTENCE_SYSTEM_PROMPT = (
    "Ты проверяешь, УТВЕРЖДАЕТ ли данный фрагмент текста связь между "
    "РОВНО ДВУМЯ объектами. Тебе даны их id, описание и evidence, и "
    "участок исходного текста.\n\n"
    "Правила, нарушать которые нельзя:\n"
    "- сам факт, что тебе дали именно эту пару, НИЧЕГО не значит: пары "
    "порождены механически по текстовой близости (соседние предложения, "
    "один абзац), не по смыслу — большинство пар, которые ты увидишь, "
    "НЕ связаны;\n"
    "- отвечай ТОЛЬКО про эту пару; не упоминай другие id, не создавай "
    "новые сущности или атомы — их не существует в этой задаче;\n"
    "- entailed=true — только если фрагмент ЯВНО утверждает связь именно "
    "между этими двумя объектами, а не просто упоминает обоих рядом;\n"
    "- entailed=false — умолчание при любом сомнении, не крайний случай;\n"
    "- если entailed=true, укажи evidence_quote — дословную цитату из "
    "ФРАГМЕНТА НИЖЕ (не из общих знаний, не пересказ), которая доказывает "
    "именно эту связь."
)

EXISTENCE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "entailed": {"type": "boolean"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["entailed"],
}

TYPING_SYSTEM_PROMPT = (
    "Связь между этими двумя объектами уже подтверждена отдельным шагом. "
    "Твоя единственная задача — выбрать её ТИП и НАПРАВЛЕНИЕ.\n\n"
    "Правила, нарушать которые нельзя:\n"
    "- отвечай ТОЛЬКО про эту пару; не упоминай другие id;\n"
    "- выбери САМЫЙ ТОЧНЫЙ подходящий тип из списка ниже;\n"
    "- related_to — последнее средство, когда связь явно есть, но ни "
    "один точный тип её не описывает, а не выбор по умолчанию;\n"
    "- direction=a_to_b, если связь идёт ОТ объекта A К объекту B (A — "
    "субъект, инициатор, обладатель); direction=b_to_a — если наоборот. "
    "Порядок появления объектов в тексте направления НЕ определяет.\n\n"
    "Разрешённые типы связи:\n"
    + "\n".join(f"- {name} — {gloss}" for name, gloss in _RELATION_GLOSS)
)

TYPING_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "relation_type": {"type": "string", "enum": sorted(semantic_extract._RELATION_TYPES)},
        "direction": {"type": "string", "enum": ["a_to_b", "b_to_a"]},
    },
    "required": ["relation_type", "direction"],
}


def _describe(object_id: str, obj: ExtractedEntity | ExtractedAtom) -> str:
    if isinstance(obj, ExtractedEntity):
        return f"{object_id} [{obj.entity_type}] {obj.label} — evidence: {obj.evidence_quote!r}"
    return f"{object_id} [{obj.kind}] {obj.title} — evidence: {obj.evidence_quote!r}"


def _existence_prompt(candidate: RelationCandidate, from_obj, to_obj, complaint: str | None) -> str:
    parts = [
        f"Объект A: {_describe(candidate.from_id, from_obj)}",
        f"Объект B: {_describe(candidate.to_id, to_obj)}",
    ]
    if complaint:
        parts.append(f"Прошлый ответ отклонён: {complaint}. Исправь и верни только объект.")
    parts.append(
        "Даны два объекта и участок исходного текста. Сам факт выбора пары "
        "НЕ означает наличие связи.\n\n"
        f"Фрагмент:\n{candidate.evidence_context}")
    return "\n\n".join(parts)


def _typing_prompt(candidate: RelationCandidate, from_obj, to_obj, evidence_quote: str,
                   complaint: str | None) -> str:
    parts = [
        f"Объект A: {_describe(candidate.from_id, from_obj)}",
        f"Объект B: {_describe(candidate.to_id, to_obj)}",
        f"Доказанное свидетельство связи: {evidence_quote!r}",
    ]
    if complaint:
        parts.append(f"Прошлый ответ отклонён: {complaint}. Исправь и верни только объект.")
    return "\n\n".join(parts)


def _as_bool(value: object) -> bool:
    """Ollama-схема просит `boolean`, но малые модели иногда всё равно
    сериализуют его строкой — `bool("false")` в Python истинно, что было
    бы тихой инверсией default'а. Строка сравнивается по значению, не по
    непустоте."""
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _parse_json_object(raw: str, *, step: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionFailed(f"{step}: невалидный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractionFailed(f"{step}: ожидался объект, пришёл {type(data).__name__}")
    return data


def _call_with_retries(prompt_fn, *, system: str, response_schema: dict, model: str,
                       keep_alive: str | None, attempts: int, step: str,
                       pair_label: str) -> tuple[dict | None, str | None]:
    """Общий retry-цикл для 2A/2B: `attempts` попыток, при провале
    отдаёт `(None, reason)` вместо исключения наружу — одна пара не
    имеет права обрушить всё окно (тот же принцип, что был у прежнего
    C2_MAX_ATTEMPTS)."""
    complaint: str | None = None
    for attempt in range(1, attempts + 1):
        prompt = prompt_fn(complaint)
        try:
            raw = semantic_extract._call_ollama(
                prompt, model=model, keep_alive=keep_alive, system=system, response_schema=response_schema)
            return _parse_json_object(raw, step=step), None
        except ExtractionFailed as exc:
            complaint = str(exc)
            logger.warning("%s пара %s не разобрана, попытка %d из %d: %s",
                           step, pair_label, attempt, attempts, exc)
    return None, f"{pair_label}: {step} не удалось разобрать за {attempts} попыток: {complaint}"


def classify_existence(candidate: RelationCandidate, *, from_obj: ExtractedEntity | ExtractedAtom,
                       to_obj: ExtractedEntity | ExtractedAtom, model: str,
                       keep_alive: str | None, attempts: int = MAX_ATTEMPTS,
                       ) -> tuple[bool, str | None, str | None]:
    """Pass 2A в изоляции — вынесена отдельно ради R4.6.E.4 (микро-
    calibration существования БЕЗ типизации: владелец «Здесь НЕТ списка
    relation types вообще») и переиспользуется `classify_relation()`
    как первый шаг. Возвращает `(entailed, evidence_quote, reject_
    reason)`:
    - `(True, quote, None)` — модель утверждает связь, evidence заземлён
      в `candidate.evidence_context`;
    - `(False, None, None)` — явный `entailed=false`, валидный default,
      не ошибка;
    - `(False, None, reason)` — до вынесения решения дошло, но ответ
      негоден (malformed JSON после всех попыток, `entailed=true` без
      evidence_quote, evidence не заземлён) — калибровка отличает этот
      случай от честного `False` через `reason`, но как метрика
      existence (precision/recall/specificity) он — тоже «not entailed»,
      predicted-negative."""
    pair_label = f"{candidate.from_id}->{candidate.to_id}"

    data, failure = _call_with_retries(
        lambda complaint: _existence_prompt(candidate, from_obj, to_obj, complaint),
        system=EXISTENCE_SYSTEM_PROMPT, response_schema=EXISTENCE_RESPONSE_SCHEMA,
        model=model, keep_alive=keep_alive, attempts=attempts, step="2A", pair_label=pair_label)
    if data is None:
        return False, None, failure

    if not _as_bool(data.get("entailed")):
        return False, None, None

    evidence_quote = str(data.get("evidence_quote") or "").strip()
    if not evidence_quote:
        return False, None, f"{pair_label}: 2A вернул entailed=true без evidence_quote"

    # Владелец: evidence — подстрока КОНТЕКСТА КАНДИДАТА, не всего окна.
    if not semantic_extract._evidence_grounded(evidence_quote, candidate.evidence_context):
        return False, None, (f"{pair_label}: evidence_quote {evidence_quote!r:.80} "
                             f"не найден в контексте кандидата")

    return True, evidence_quote, None


def classify_relation(candidate: RelationCandidate, *, from_obj: ExtractedEntity | ExtractedAtom,
                      to_obj: ExtractedEntity | ExtractedAtom, model: str,
                      keep_alive: str | None, attempts: int = MAX_ATTEMPTS,
                      ) -> tuple[ExtractedEdge | None, str | None]:
    """Классифицирует ОДНУ пару в два независимых вызова — существование
    (2A, `classify_existence()`), затем при `entailed=true` тип и
    направление (2B). Возвращает `(edge, None)` при явной типизированной
    связи, `(None, None)` при `entailed=false` (валидный, ожидаемый
    исход, не ошибка), `(None, reason)` при отбрасывании на любом из
    двух шагов — `reason` для диагностики, тем же принципом, что
    `WindowExtraction.rejected` у single/two-pass."""
    pair_label = f"{candidate.from_id}->{candidate.to_id}"

    entailed, evidence_quote, failure = classify_existence(
        candidate, from_obj=from_obj, to_obj=to_obj, model=model, keep_alive=keep_alive, attempts=attempts)
    if not entailed:
        return None, failure

    data2, failure2 = _call_with_retries(
        lambda complaint: _typing_prompt(candidate, from_obj, to_obj, evidence_quote, complaint),
        system=TYPING_SYSTEM_PROMPT, response_schema=TYPING_RESPONSE_SCHEMA,
        model=model, keep_alive=keep_alive, attempts=attempts, step="2B", pair_label=pair_label)
    if data2 is None:
        return None, f"{pair_label}: связь подтверждена (2A), но не типизирована — {failure2}"

    relation = str(data2.get("relation_type") or "").strip().lower()
    direction = str(data2.get("direction") or "").strip().lower()

    if relation not in semantic_extract._RELATION_TYPES:
        return None, f"{pair_label}: 2B вернул тип вне реестра {relation!r} — отклонено, не related_to"
    if direction not in ("a_to_b", "b_to_a"):
        return None, f"{pair_label}: 2B вернул невалидное направление {direction!r}"

    from_id, to_id = (
        (candidate.from_id, candidate.to_id) if direction == "a_to_b"
        else (candidate.to_id, candidate.from_id))
    return ExtractedEdge(from_local_id=from_id, relation_type=relation, to_local_id=to_id,
                         evidence_quote=evidence_quote), None
