"""v4.0 §14.4.2/§14.4.3 — контракт вывода извлекателя и его проверка.

Semantic-v1 возвращал «список красивых заметок»: плоские
`slug/type/text/links`, где связь была строкой-именем, дата — частью
текста, а происхождение отсутствовало. §14.4 требует другого: сущности,
атомы и ТИПИЗИРОВАННЫЕ рёбра между ними, со структурной датой.

Ключевое отличие от v1 — `local_id`. Модель называет свои находки
внутри одного окна и связывает их по этим именам; совпадение подписей
между окнами и между источниками её не касается. Кто с кем одно лицо,
решает разрешение сущностей (§14.7, шаг R6), а не совпадение строки в
ответе модели, — иначе однофамильцы склеиваются ровно так же, как
склеивались в v1 по slug.

Только локально (§14.4.3): Ollama, никакого LiteLLM/OpenRouter.
Невалидный ответ — ограниченное число попыток починки, дальше окно
FAILED. Облачного запасного пути нет и не появится: на этом материале
он означал бы отправку медицинских документов наружу.

Выбор модели здесь НЕ делается. `gemma2:2b` стоит значением по
умолчанию только потому, что уже поднята на сервере; §14.18 и шаг R4
требуют отдельного замера именно на извлечении. Модуль принимает имя
модели снаружи, чтобы R4 менял его настройкой, а не правкой кода.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..models.base import (
    SemanticDatePrecision, SemanticNodeKind, SemanticRelationType,
)

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://ollama:11434/api/generate"
#: Временное значение, не выбор. Решает R4 (§14.18).
DEFAULT_MODEL = "gemma2:2b"
REQUEST_TIMEOUT = 120
KEEP_ALIVE = "0"

#: Потолок на ОКНО, а не на источник. Упёрлись — окно делится и
#: перезапускается (§14.4.1); молча отбросить остаток нельзя, и именно
#: это делал `data[:MAX_ATOMS_PER_CALL]` в semantic-v1.
MAX_ATOMS_PER_WINDOW = 40

#: Сколько раз чинить невалидный ответ, прежде чем признать окно
#: провалившимся. Три — не магия: первая попытка обычная, вторая с
#: явным указанием на ошибку, третья последняя. Больше означало бы
#: минуты CPU на одном окне ради всё того же ответа.
MAX_REPAIR_ATTEMPTS = 3


class ExtractionFailed(RuntimeError):
    """Окно не удалось разобрать после всех попыток."""


class WindowTruncated(RuntimeError):
    """Окно упёрлось в потолок атомов — его надо разделить, а не обрезать."""


@dataclass(frozen=True)
class ExtractedEntity:
    local_id: str
    entity_type: str
    label: str
    subtype: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractedAtom:
    local_id: str
    kind: str
    title: str
    text: str
    subtype: str | None = None
    occurred_at: str | None = None
    date_precision: str | None = None


@dataclass(frozen=True)
class ExtractedEdge:
    from_local_id: str
    relation_type: str
    to_local_id: str
    role: str | None = None


@dataclass
class WindowExtraction:
    entities: list[ExtractedEntity] = field(default_factory=list)
    atoms: list[ExtractedAtom] = field(default_factory=list)
    edges: list[ExtractedEdge] = field(default_factory=list)
    #: Что выброшено на проверке и почему. Не лог, а поле результата:
    #: §14.4.1 требует отличать «модель ничего не нашла» от «модель
    #: вернула мусор, который мы молча отбросили».
    rejected: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.entities and not self.atoms


#: Форма ответа задаётся СХЕМОЙ, а не просьбой в промпте. Живой замер
#: 02.09.2026: с `format: "json"` gemma2:2b возвращает ОДИН объект —
#: Ollama гарантирует валидный JSON, но не его форму; со схемой
#: возвращает то, что просили.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "local_id": {"type": "string"},
                    "entity_type": {"type": "string"},
                    "subtype": {"type": "string"},
                    "label": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["local_id", "entity_type", "label"],
            },
        },
        "atoms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "local_id": {"type": "string"},
                    "kind": {"type": "string"},
                    "subtype": {"type": "string"},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "occurred_at": {"type": "string"},
                    "date_precision": {"type": "string"},
                },
                "required": ["local_id", "kind", "title", "text"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "type": {"type": "string"},
                    "to": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["from", "type", "to"],
            },
        },
    },
    "required": ["entities", "atoms", "edges"],
}

#: Виды, которые модель может выдать за атом. DOCUMENT_REF/MEMORY_REF
#: сюда не входят намеренно: это узлы-личности документа и памяти,
#: заводимые детерминированно, а не извлекаемые из текста (решение
#: 02.09.2026, разбор в models/base.py).
_ATOM_KINDS = {
    SemanticNodeKind.EVENT.value, SemanticNodeKind.FACT.value,
    SemanticNodeKind.DECISION.value, SemanticNodeKind.CONCEPT.value,
}
_RELATION_TYPES = {member.value for member in SemanticRelationType}
_DATE_PRECISIONS = {member.value for member in SemanticDatePrecision}

SYSTEM_PROMPT = (
    "Ты — детерминированный извлекатель структурированных знаний из личного "
    "архива владельца: здоровье, работа, покупки, обучение, встречи, проекты. "
    "Работаешь с ОДНИМ фрагментом документа.\n\n"
    "Верни объект с тремя списками.\n"
    "entities — участники: люди, организации, места, понятия. У сущности "
    "только личность: кто это, а не что с ним произошло.\n"
    "atoms — отдельные утверждения из текста: EVENT (событие), FACT (факт), "
    "DECISION (решение), CONCEPT (описание понятия). Одно утверждение — один "
    "атом.\n"
    "edges — связи между ними по local_id.\n\n"
    "Правила, нарушать которые нельзя:\n"
    "- пиши только то, что сказано в тексте буквально; не додумывай, не "
    "оценивай, не советуй;\n"
    "- даты пиши в occurred_at как ГГГГ-ММ-ДД, ГГГГ-ММ или ГГГГ и ставь "
    "date_precision day/month/year; относительную дату («в прошлый вторник»), "
    "которую не к чему привязать, оставь пустой с date_precision unknown — "
    "выдумывать точную дату запрещено;\n"
    "- local_id уникальны внутри этого ответа; в edges ссылайся только на них;\n"
    "- если во фрагменте нечего извлекать, верни три пустых списка."
)


def _prompt(window_text: str, *, domain: str, heading_path: tuple[str, ...],
            complaint: str | None) -> str:
    """Заголовки идут в промпт отдельной строкой: абзац «показатель в
    норме» без раздела «Биохимия» не сообщает, какой показатель."""
    parts = [f"Домен: {domain}"]
    if heading_path:
        parts.append("Раздел: " + " → ".join(heading_path))
    if complaint:
        # Попытка починки называет ошибку прямо. Повтор того же промпта
        # даёт тот же ответ — чинить надо тем, чего в прошлый раз не
        # хватило, иначе повторы просто жгут CPU.
        parts.append(f"Прошлый ответ отклонён: {complaint}. Исправь и верни только объект.")
    parts.append(f"Фрагмент:\n{window_text}")
    return "\n\n".join(parts)


def _call_ollama(prompt: str, *, model: str) -> str:
    body = {
        "model": model,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "format": RESPONSE_SCHEMA,
    }
    request = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ExtractionFailed(f"извлекатель недоступен: {exc}") from exc
    answer = (payload.get("response") or "").strip()
    if not answer:
        raise ExtractionFailed("извлекатель вернул пустой ответ")
    return answer


def validate(raw: str) -> WindowExtraction:
    """Разобрать и проверить ответ модели.

    Проверка не «на всякий случай»: §14.4.3 называет её обязательной.
    Отброшенное записывается в `rejected` — счёт выброшенного и есть
    разница между «модель ничего не нашла» и «мы молча съели мусор».

    Неизвестный тип связи не отбрасывается, а нормализуется к
    RELATED_TO (§14.9: «неизвестный тип нормализуется к реестру либо
    становится RELATED_TO с сохранённым свидетельством»). Ребро,
    указывающее в никуда, отбрасывается — оно не связь, а опечатка.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionFailed(f"невалидный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractionFailed(f"ожидался объект, пришёл {type(data).__name__}")

    result = WindowExtraction()
    known: set[str] = set()

    for item in data.get("entities") or []:
        if not isinstance(item, dict):
            result.rejected.append("сущность не объект")
            continue
        local_id = str(item.get("local_id") or "").strip()
        label = str(item.get("label") or "").strip()
        entity_type = str(item.get("entity_type") or "").strip().upper()
        if not local_id or not label or not entity_type:
            result.rejected.append(f"сущность без обязательного поля: {item!r:.80}")
            continue
        if local_id in known:
            result.rejected.append(f"повтор local_id {local_id!r}")
            continue
        known.add(local_id)
        aliases = tuple(str(a).strip() for a in item.get("aliases") or [] if str(a).strip())
        result.entities.append(ExtractedEntity(
            local_id=local_id, entity_type=entity_type, label=label,
            subtype=(str(item.get("subtype")).strip() or None) if item.get("subtype") else None,
            aliases=aliases))

    for item in data.get("atoms") or []:
        if not isinstance(item, dict):
            result.rejected.append("атом не объект")
            continue
        local_id = str(item.get("local_id") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "").strip()
        if not local_id or not title or not text:
            result.rejected.append(f"атом без обязательного поля: {item!r:.80}")
            continue
        if kind not in _ATOM_KINDS:
            result.rejected.append(f"вид {kind!r} вне реестра")
            continue
        if local_id in known:
            result.rejected.append(f"повтор local_id {local_id!r}")
            continue
        known.add(local_id)
        precision = str(item.get("date_precision") or "").strip().lower() or None
        if precision and precision not in _DATE_PRECISIONS:
            # Точность вне реестра — не повод терять весь атом: сама
            # дата остаётся, точность становится «неизвестна». §14.8
            # запрещает выдумывать точность, а не хранить дату.
            result.rejected.append(f"точность даты {precision!r} вне реестра")
            precision = SemanticDatePrecision.UNKNOWN.value
        result.atoms.append(ExtractedAtom(
            local_id=local_id, kind=kind, title=title, text=text,
            subtype=(str(item.get("subtype")).strip() or None) if item.get("subtype") else None,
            occurred_at=(str(item.get("occurred_at")).strip() or None)
                        if item.get("occurred_at") else None,
            date_precision=precision))

    for item in data.get("edges") or []:
        if not isinstance(item, dict):
            result.rejected.append("связь не объект")
            continue
        source = str(item.get("from") or "").strip()
        target = str(item.get("to") or "").strip()
        relation = str(item.get("type") or "").strip().lower()
        if source not in known or target not in known:
            result.rejected.append(f"связь в никуда: {source!r} → {target!r}")
            continue
        if relation not in _RELATION_TYPES:
            result.rejected.append(f"тип связи {relation!r} сведён к related_to")
            relation = SemanticRelationType.RELATED_TO.value
        result.edges.append(ExtractedEdge(
            from_local_id=source, relation_type=relation, to_local_id=target,
            role=(str(item.get("role")).strip() or None) if item.get("role") else None))

    if len(result.atoms) >= MAX_ATOMS_PER_WINDOW:
        raise WindowTruncated(
            f"окно дало {len(result.atoms)} атомов при потолке {MAX_ATOMS_PER_WINDOW}")
    return result


def extract_window(window_text: str, *, domain: str, heading_path: tuple[str, ...] = (),
                   model: str = DEFAULT_MODEL,
                   attempts: int = MAX_REPAIR_ATTEMPTS) -> WindowExtraction:
    """Разобрать одно окно с ограниченным числом попыток починки.

    `WindowTruncated` НЕ ловится: это не сбой модели, а сигнал звену
    выше поделить окно. Попытка «починить» переполнение повтором дала бы
    ровно тот же результат, только медленнее.
    """
    complaint: str | None = None
    for attempt in range(1, attempts + 1):
        prompt = _prompt(window_text, domain=domain, heading_path=heading_path,
                         complaint=complaint)
        try:
            return validate(_call_ollama(prompt, model=model))
        except WindowTruncated:
            raise
        except ExtractionFailed as exc:
            complaint = str(exc)
            logger.warning("окно не разобрано, попытка %d из %d: %s", attempt, attempts, exc)
    raise ExtractionFailed(f"не удалось разобрать окно за {attempts} попыток: {complaint}")
