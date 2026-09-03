"""R4.6.C — two-pass extraction эксперимент (владелец 03.09.2026, условно
разрешён исходным мандатом п.6.C).

R4.6.B разложил `relation_precision=0.28` (qwen2.5:7b, run 210/217) по
типам ошибок: `correct=0`, `extra_relation=11` и `extra_due_to_entity_
mismatch=5` — то есть матрица precision упирается почти целиком в
ЛОЖНЫЕ связи (16 из 23 знаменателя), а не в терминологию. Модель,
решая одновременно «что тут есть» и «кто с кем связан» в одном
проходе, в условиях неопределённости выбирает «предположительно
связаны», а не молчание. Требуемое сокращение (~94–100% ложных связей)
слишком велико для точечного prompt-фикса на single-pass.

Этот модуль — НЕ замена production-пути. `extract_window()` из
`semantic_extract.py` остаётся единственным путём, которым пользуется
всё остальное (ingest, R3 атомизатор). `extract_window_two_pass()`
существует только для того, чтобы R4.6 сравнил его метрики с
single-pass на golden-наборе — решение о переключении production-пути
принимается ПОСЛЕ этого сравнения, не здесь.

Архитектура:
  pass 1 — grounded entities + atoms. Технически это полный
    `extract_window()` (тот же вызов, что у single-pass) — его `edges`
    отбрасываются: право решать связи здесь не у него. Не отдельный
    вызов с урезанной схемой ради экономии одного HTTP-запроса —
    цена (модель всё равно пытается предложить edges, они просто не
    используются) меньше риска второй, отдельно поддерживаемой копии
    entity/atom-валидации, которая рано или поздно разъедется с
    production.
  pass 2 — типизация связей СТРОГО между local_id, уже найденными в
    pass 1, видя их evidence_quote и исходное окно. Не может создать
    новый local_id: `_validate_edges()` (общая с single-pass,
    `semantic_extract.py`) отбрасывает любую связь на id вне
    переданного `known` — структурный запрет, не обещание в промпте
    (тот же принцип, что и evidence grounding в R4.5.3).
"""

from __future__ import annotations

import json
import logging

from . import semantic_extract
from .semantic_extract import (
    MAX_REPAIR_ATTEMPTS,
    DEFAULT_MODEL,
    ExtractedAtom,
    ExtractedEdge,
    ExtractedEntity,
    ExtractionFailed,
    WindowExtraction,
)

logger = logging.getLogger(__name__)

#: Закрытый реестр §14.9 с однострочным пояснением каждого типа — R4.6.B
#: нашёл, что модель систематически предлагает более точные, но вне
#: реестра синонимы (`performed_by`, `attended_by`, `causes`,
#: `purchased_at`), которые `_validate_edges()` молча сводит к
#: `related_to`. Явные глоссы — попытка направить выбор модели В реестр,
#: а не терпимость к `related_to` как норме.
_RELATION_GLOSS = (
    ("involves", "атом или сущность прямо задействует другую сущность (участник события/факта)"),
    ("has_role", "сущность занимает роль по отношению к другой сущности"),
    ("about", "атом описывает или касается понятия либо сущности"),
    ("located_at", "атом или сущность привязаны к месту"),
    ("part_of", "сущность или атом — часть другой сущности/атома"),
    ("created_by", "атом или сущность созданы, сделаны или составлены кем-то"),
    ("owned_by", "сущность или атом принадлежат кому-то"),
    ("resulted_in", "атом привёл к другому атому или факту"),
    ("reason_for", "атом — причина или обоснование другого атома"),
    ("supports", "атом подтверждает или обосновывает другой атом"),
    ("contradicts", "атом противоречит другому атому"),
)

PASS2_SYSTEM_PROMPT = (
    "Ты — вторая стадия извлечения знаний. Сущности и атомы этого "
    "фрагмента УЖЕ найдены и перечислены тебе с их local_id и evidence. "
    "Твоя единственная задача — типизировать связи МЕЖДУ этими id.\n\n"
    "Правила, нарушать которые нельзя:\n"
    "- используй ТОЛЬКО перечисленные тебе local_id; не придумывай новые "
    "сущности, атомы или id — эта стадия не имеет права на новые факты;\n"
    "- тип связи выбирай из закрытого списка ниже, самый точный "
    "подходящий; не подставляй related_to по умолчанию, если в списке "
    "есть более точный вариант — related_to допустим, только когда "
    "текст на самом деле не даёт связи точнее;\n"
    "- если между двумя объектами в тексте нет ЯВНОЙ связи — не создавай "
    "её вовсе; меньше связей лучше, чем предположительные;\n"
    "- у каждой связи заполняй evidence_quote — дословную цитату из "
    "фрагмента, которая доказывает именно эту связь;\n"
    "- если явных связей нет вовсе, верни пустой список.\n\n"
    "Разрешённые типы связи:\n"
    + "\n".join(f"- {name} — {gloss}" for name, gloss in _RELATION_GLOSS)
)

#: Только edges — pass 2 структурно не может вернуть entities/atoms:
#: схема их не описывает, а `_validate_edges()` не читает ничего, кроме
#: `edges`, даже если модель всё равно что-то пририсует лишнее.
PASS2_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"edges": semantic_extract.RESPONSE_SCHEMA["properties"]["edges"]},
    "required": ["edges"],
}


def _describe_objects(entities: list[ExtractedEntity], atoms: list[ExtractedAtom]) -> str:
    lines = []
    if entities:
        lines.append("Сущности:")
        lines.extend(f"- {e.local_id} [{e.entity_type}] {e.label} — evidence: {e.evidence_quote!r}"
                     for e in entities)
    if atoms:
        lines.append("Атомы:")
        lines.extend(f"- {a.local_id} [{a.kind}] {a.title} — evidence: {a.evidence_quote!r}"
                     for a in atoms)
    return "\n".join(lines)


def _pass2_prompt(window_text: str, *, domain: str, heading_path: tuple[str, ...],
                  entities: list[ExtractedEntity], atoms: list[ExtractedAtom],
                  complaint: str | None) -> str:
    parts = [f"Домен: {domain}"]
    if heading_path:
        parts.append("Раздел: " + " → ".join(heading_path))
    if complaint:
        parts.append(f"Прошлый ответ отклонён: {complaint}. Исправь и верни только объект.")
    parts.append(_describe_objects(entities, atoms))
    parts.append(f"Фрагмент:\n{window_text}")
    return "\n\n".join(parts)


def _pass2_validate(raw: str, *, known: set[str],
                    window_text: str) -> tuple[list[ExtractedEdge], list[str]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionFailed(f"pass 2: невалидный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractionFailed(f"pass 2: ожидался объект, пришёл {type(data).__name__}")
    return semantic_extract._validate_edges(data.get("edges") or [], known=known, window_text=window_text)


def _extract_relations_pass2(window_text: str, *, entities: list[ExtractedEntity],
                             atoms: list[ExtractedAtom], domain: str,
                             heading_path: tuple[str, ...] = (), model: str,
                             keep_alive: str | None,
                             attempts: int) -> tuple[list[ExtractedEdge], list[str]]:
    known = {e.local_id for e in entities} | {a.local_id for a in atoms}
    if not known:
        # Нечего связывать — pass 1 не нашёл ни одной сущности/атома.
        return [], []

    complaint: str | None = None
    for attempt in range(1, attempts + 1):
        prompt = _pass2_prompt(window_text, domain=domain, heading_path=heading_path,
                               entities=entities, atoms=atoms, complaint=complaint)
        try:
            raw = semantic_extract._call_ollama(
                prompt, model=model, keep_alive=keep_alive,
                system=PASS2_SYSTEM_PROMPT, response_schema=PASS2_RESPONSE_SCHEMA)
            return _pass2_validate(raw, known=known, window_text=window_text)
        except ExtractionFailed as exc:
            complaint = str(exc)
            logger.warning("pass 2 не разобран, попытка %d из %d: %s", attempt, attempts, exc)
    raise ExtractionFailed(f"pass 2 не удалось разобрать за {attempts} попыток: {complaint}")


def extract_window_two_pass(window_text: str, *, domain: str, heading_path: tuple[str, ...] = (),
                            model: str = DEFAULT_MODEL, keep_alive: str | None = None,
                            attempts: int = MAX_REPAIR_ATTEMPTS) -> WindowExtraction:
    """R4.6.C эксперимент — см. docstring модуля. `WindowTruncated` из
    pass 1 не ловится и распространяется как есть: окно, упёршееся в
    потолок атомов, нужно делить независимо от того, что решит pass 2."""
    pass1 = semantic_extract.extract_window(window_text, domain=domain, heading_path=heading_path,
                                            model=model, keep_alive=keep_alive, attempts=attempts)
    edges, pass2_rejected = _extract_relations_pass2(
        window_text, entities=pass1.entities, atoms=pass1.atoms, domain=domain,
        heading_path=heading_path, model=model, keep_alive=keep_alive, attempts=attempts)
    return WindowExtraction(entities=pass1.entities, atoms=pass1.atoms, edges=edges,
                            rejected=pass1.rejected + pass2_rejected)
