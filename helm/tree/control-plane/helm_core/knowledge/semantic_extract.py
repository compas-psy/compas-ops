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
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace

from ..config import get_settings
from ..models.base import (
    SemanticDatePrecision, SemanticNodeKind, SemanticRelationType,
)

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://ollama:11434/api/generate"
#: Временное значение, не выбор. Решает R4 (§14.18).
DEFAULT_MODEL = "gemma2:2b"
REQUEST_TIMEOUT = 120

#: R4 п.4: «Production extraction deterministic насколько позволяет backend:
#: temperature=0, fixed seed where supported». Без этого один и тот же
#: источник дал бы разный граф при каждом reprocess (§14.20), а сравнение
#: кандидатов в бенчмарке было бы шумом, а не сигналом.
DETERMINISTIC_SEED = 0

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


class ExtractionTimedOut(ExtractionFailed):
    """Транспортный таймаут вызова Ollama (P2, владелец 2026-09-04, R4 RCA
    run 241: `long_dense_window` — 3 identical 120-секундных попытки подряд
    дали 360.32с и 0 новой информации). При temperature=0 + fixed seed
    идентичный повтор того же запроса — не починка, а гарантированный тот
    же таймаут медленнее. Отличается от `ExtractionFailed` тем, что не
    чинится repair-retry с тем же текстом окна — сигнал `extract_nodes_window()`
    поделить окно и повторить на частях."""


class WindowTruncated(RuntimeError):
    """Окно упёрлось в потолок атомов — его надо разделить, а не обрезать."""


@dataclass(frozen=True)
class ExtractedEntity:
    local_id: str
    entity_type: str
    label: str
    subtype: str | None = None
    aliases: tuple[str, ...] = ()
    evidence_quote: str = ""


@dataclass(frozen=True)
class ExtractedAtom:
    local_id: str
    kind: str
    title: str
    text: str
    subtype: str | None = None
    occurred_at: str | None = None
    date_precision: str | None = None
    evidence_quote: str = ""


@dataclass(frozen=True)
class ExtractedEdge:
    from_local_id: str
    relation_type: str
    to_local_id: str
    role: str | None = None
    evidence_quote: str = ""


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
                    "evidence_quote": {"type": "string"},
                },
                "required": ["local_id", "entity_type", "label", "evidence_quote"],
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
                    "evidence_quote": {"type": "string"},
                },
                "required": ["local_id", "kind", "title", "text", "evidence_quote"],
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
                    "evidence_quote": {"type": "string"},
                },
                "required": ["from", "type", "to", "evidence_quote"],
            },
        },
    },
    "required": ["entities", "atoms", "edges"],
}

#: P1 (владелец 2026-09-04) — production-путь и final acceptance больше не
#: просят модель за один вызов сразу entities+atoms+edges: relation_compiler.py
#: уже единственный источник рёбер (`extraction.edges` модели перезаписывается
#: в `_run_case()`/`run_shadow_benchmark()` целиком), значит просьба вернуть
#: edges — не проверяемый контракт, а архитектурное противоречие и лишняя
#: когнитивная нагрузка на модель. Схема без `edges` вовсе — не «edges:
#: []», а отсутствие поля: модель не тратит внимание на связи, которые
#: всё равно будут выброшены. `extract_window()` (со старой схемой, с
#: edges) остаётся нетронутым — им пользуются только historical/
#: experimental пути (`semantic_extract_c2.py`, `semantic_extract_twopass.py`)
#: и их старые benchmark artifacts.
#:
#: `entity_type`/`kind` — strict enum прямо в схеме (не проверка постфактум
#: в парсере): Ollama `format`-constrained decoding не даёт модели физически
#: вернуть значение вне перечня. PERSON/ORGANIZATION/PLACE/CONCEPT — не новый
#: список, а уже единственные 4 значения, встречающиеся во всём golden/frozen
#: корпусе (проверено программно по semantic_benchmark_fixtures.py и
#: relation_benchmark_v3_fixtures.py). Парсинг ответа — не `validate()`
#: (владелец, R4 P10 2026-09-04): у node-only свой `_validate_nodes()`,
#: см. ниже.
_ENTITY_TYPES = ("PERSON", "ORGANIZATION", "PLACE", "CONCEPT")

NODE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "local_id": {"type": "string"},
                    "entity_type": {"type": "string", "enum": list(_ENTITY_TYPES)},
                    "subtype": {"type": "string"},
                    "label": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["local_id", "entity_type", "label", "evidence_quote"],
            },
        },
        "atoms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "local_id": {"type": "string"},
                    "kind": {"type": "string", "enum": [
                        SemanticNodeKind.EVENT.value, SemanticNodeKind.FACT.value,
                        SemanticNodeKind.DECISION.value, SemanticNodeKind.CONCEPT.value,
                    ]},
                    "subtype": {"type": "string"},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "occurred_at": {"type": "string"},
                    "date_precision": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["local_id", "kind", "title", "text", "evidence_quote"],
            },
        },
    },
    "required": ["entities", "atoms"],
}

NODE_SYSTEM_PROMPT = (
    "Ты — детерминированный извлекатель структурированных знаний из личного "
    "архива владельца: здоровье, работа, покупки, обучение, встречи, проекты. "
    "Работаешь с ОДНИМ фрагментом документа.\n\n"
    "Верни объект с двумя списками.\n"
    "entities — участники: люди, организации, места, понятия. У сущности "
    "только личность: кто это, а не что с ним произошло. entity_type строго "
    "один из четырёх:\n"
    "PERSON = конкретный человек;\n"
    "ORGANIZATION = организация/учреждение как субъект;\n"
    "PLACE = физическое место/локация события;\n"
    "CONCEPT = понятие, специальность, категория, термин.\n"
    "atoms — отдельные утверждения из текста: EVENT (событие), FACT (факт), "
    "DECISION (решение), CONCEPT (описание понятия). Одно утверждение — один "
    "атом.\n\n"
    "Правила, нарушать которые нельзя:\n"
    "- пиши только то, что сказано в тексте буквально; не додумывай, не "
    "оценивай, не советуй;\n"
    "- даты пиши в occurred_at как ГГГГ-ММ-ДД, ГГГГ-ММ или ГГГГ и ставь "
    "date_precision day/month/year; относительную дату («в прошлый вторник»), "
    "которую не к чему привязать, оставь пустой с date_precision unknown — "
    "выдумывать точную дату запрещено;\n"
    "- local_id уникальны внутри этого ответа;\n"
    "- у каждой сущности и атома заполняй evidence_quote — дословную цитату "
    "из фрагмента (без пересказа и обобщения), которая доказывает именно эту "
    "находку; цитата должна быть точной подстрокой фрагмента;\n"
    "- служебный текст документа (колонтитул, номер страницы, штамп версии "
    "шаблона, «не редактировать вручную» и подобное форматирование) не "
    "несёт долговременного знания о владельце — по нему верни два пустых "
    "списка, даже если формально это предложение с подлежащим и сказуемым;\n"
    "- если во фрагменте нечего извлекать, верни два пустых списка."
)

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
_PRECISE_DATE_PRECISIONS = {
    SemanticDatePrecision.DAY.value, SemanticDatePrecision.MONTH.value,
    SemanticDatePrecision.YEAR.value,
}

_WS = re.compile(r"\s+")

#: Тот же паттерн, что в semantic_benchmark_metrics.py — не импортируется
#: оттуда намеренно, чтобы валидатор извлечения не зависел от модуля
#: бенчмарка.
_NEGATION_RE = re.compile(r"\bне\b|\bнет\b|\bнельзя\b", re.IGNORECASE)

#: Владелец 03.09.2026: relative unanchored date (нельзя привязать к
#: абсолютной дате) обязана остаться date_precision=unknown — эвристика
#: маркеров относительного времени, по которым evidence_quote уличает
#: occurred_at, выставленный туда, где в тексте только «в прошлый вторник».
_RELATIVE_DATE_MARKERS_RE = re.compile(
    r"\bв прошл\w+|\bна прошл\w+|\bв следующ\w+|\bна следующ\w+|"
    r"\bна днях\b|\bнедавно\b|\bдавно\b|\bвчера\b|\bпозавчера\b|"
    r"\bзавтра\b|\bпослезавтра\b|\bскоро\b",
    re.IGNORECASE,
)

#: Абсолютная дата в evidence: цифровая (ISO/ДД.ММ.ГГГГ/год) либо
#: русское название месяца. Подтверждает, что precise occurred_at не
#: выдуман, а взят из буквального текста фрагмента.
_ABSOLUTE_DATE_RE = re.compile(
    r"\d{4}|\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?|"
    r"январ\w*|феврал\w*|март\w*|апрел\w*|ма[ей]\w*|июн\w*|июл\w*|"
    r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*",
    re.IGNORECASE,
)


def _evidence_grounded(evidence_quote: str, window_text: str) -> bool:
    """§14.4.2 grounding: evidence_quote обязан быть дословной (с точностью
    до пробелов) подстрокой окна, а не пересказом."""
    return _WS.sub(" ", evidence_quote) in _WS.sub(" ", window_text)


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
    "- у каждой сущности, атома и связи заполняй evidence_quote — дословную "
    "цитату из фрагмента (без пересказа и обобщения), которая доказывает "
    "именно эту находку; цитата должна быть точной подстрокой фрагмента;\n"
    "- служебный текст документа (колонтитул, номер страницы, штамп версии "
    "шаблона, «не редактировать вручную» и подобное форматирование) не "
    "несёт долговременного знания о владельце — по нему верни три пустых "
    "списка, даже если формально это предложение с подлежащим и сказуемым;\n"
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


def _call_ollama(prompt: str, *, model: str, keep_alive: str | None = None,
                  system: str = SYSTEM_PROMPT, response_schema: dict = RESPONSE_SCHEMA) -> str:
    """`system`/`response_schema` параметризованы ради R4.6.C
    (`semantic_extract_twopass.py`): pass 2 того эксперимента — другой
    промпт и схема (только edges), но тот же HTTP/retry/детерминизм
    контракт, что и у single-pass. Значения по умолчанию — прежнее
    поведение `extract_window()`, ничего не меняется для него."""
    body = {
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "keep_alive": keep_alive if keep_alive is not None else get_settings().knowledge_semantic_keep_alive,
        "format": response_schema,
        "options": {"temperature": 0, "seed": DETERMINISTIC_SEED},
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


def _validate_edges(edge_items, *, known: set[str], window_text: str) -> tuple[list[ExtractedEdge], list[str]]:
    """Общая проверка edges — вынесена из `validate()` ради R4.6.C
    (`semantic_extract_twopass.py`): pass 2 того эксперимента строит
    `known` не из своего же ответа (там нет entities/atoms), а из
    результатов pass 1, и вызывает ЭТУ ЖЕ функцию, а не копию её
    логики. Держать grounding/реестр §14.9 в двух местах значило бы
    разъезжаться при следующей правке (тот же класс риска, что и
    остальные дублирующиеся проверки в этой сессии)."""
    edges: list[ExtractedEdge] = []
    rejected: list[str] = []
    for item in edge_items:
        if not isinstance(item, dict):
            rejected.append("связь не объект")
            continue
        source = str(item.get("from") or "").strip()
        target = str(item.get("to") or "").strip()
        relation = str(item.get("type") or "").strip().lower()
        if source not in known or target not in known:
            rejected.append(f"связь в никуда: {source!r} → {target!r}")
            continue
        evidence_quote = str(item.get("evidence_quote") or "").strip()
        if not evidence_quote:
            rejected.append(f"связь без evidence_quote: {item!r:.80}")
            continue
        if not _evidence_grounded(evidence_quote, window_text):
            rejected.append(f"evidence_quote связи не найден в тексте окна: {evidence_quote!r:.80}")
            continue
        if relation not in _RELATION_TYPES:
            rejected.append(f"тип связи {relation!r} сведён к related_to")
            relation = SemanticRelationType.RELATED_TO.value
        edges.append(ExtractedEdge(
            from_local_id=source, relation_type=relation, to_local_id=target,
            role=(str(item.get("role")).strip() or None) if item.get("role") else None,
            evidence_quote=evidence_quote))
    return edges, rejected


def validate(raw: str, *, window_text: str) -> WindowExtraction:
    """Разобрать и проверить ответ модели.

    Проверка не «на всякий случай»: §14.4.3 называет её обязательной.
    Отброшенное записывается в `rejected` — счёт выброшенного и есть
    разница между «модель ничего не нашла» и «мы молча съели мусор».

    Неизвестный тип связи не отбрасывается, а нормализуется к
    RELATED_TO (§14.9: «неизвестный тип нормализуется к реестру либо
    становится RELATED_TO с сохранённым свидетельством»). Ребро,
    указывающее в никуда, отбрасывается — оно не связь, а опечатка.

    Владелец 03.09.2026: словами в промпте безопасность не обеспечить —
    каждая сущность/атом/связь обязаны нести evidence_quote, дословно
    входящий в `window_text`; иначе элемент отбрасывается целиком, не
    понижается «на всякий случай». Это validation layer, а не точный
    provenance (char_start/char_end откладывается в R5).
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
        evidence_quote = str(item.get("evidence_quote") or "").strip()
        if not evidence_quote:
            result.rejected.append(f"сущность без evidence_quote: {item!r:.80}")
            continue
        if not _evidence_grounded(evidence_quote, window_text):
            result.rejected.append(f"evidence_quote сущности не найден в тексте окна: {evidence_quote!r:.80}")
            continue
        known.add(local_id)
        aliases = tuple(str(a).strip() for a in item.get("aliases") or [] if str(a).strip())
        result.entities.append(ExtractedEntity(
            local_id=local_id, entity_type=entity_type, label=label,
            subtype=(str(item.get("subtype")).strip() or None) if item.get("subtype") else None,
            aliases=aliases, evidence_quote=evidence_quote))

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
        evidence_quote = str(item.get("evidence_quote") or "").strip()
        if not evidence_quote:
            result.rejected.append(f"атом без evidence_quote: {item!r:.80}")
            continue
        if not _evidence_grounded(evidence_quote, window_text):
            result.rejected.append(f"evidence_quote атома не найден в тексте окна: {evidence_quote!r:.80}")
            continue
        if _NEGATION_RE.search(evidence_quote) and not _NEGATION_RE.search(text):
            result.rejected.append(f"отрицание есть в evidence, но потеряно в тексте атома: {text!r:.80}")
            continue

        precision = str(item.get("date_precision") or "").strip().lower() or None
        if precision and precision not in _DATE_PRECISIONS:
            # Точность вне реестра — не повод терять весь атом: сама
            # дата остаётся, точность становится «неизвестна». §14.8
            # запрещает выдумывать точность, а не хранить дату.
            result.rejected.append(f"точность даты {precision!r} вне реестра")
            precision = SemanticDatePrecision.UNKNOWN.value
        occurred_at = (str(item.get("occurred_at")).strip() or None) if item.get("occurred_at") else None

        if occurred_at and _RELATIVE_DATE_MARKERS_RE.search(evidence_quote):
            result.rejected.append(
                f"evidence описывает относительную дату, но occurred_at выставлен: {occurred_at!r}")
            continue
        if occurred_at and precision in _PRECISE_DATE_PRECISIONS and not _ABSOLUTE_DATE_RE.search(evidence_quote):
            result.rejected.append(
                f"occurred_at {occurred_at!r} не подтверждён абсолютной датой в evidence: "
                f"{evidence_quote!r:.80}")
            continue

        known.add(local_id)
        result.atoms.append(ExtractedAtom(
            local_id=local_id, kind=kind, title=title, text=text,
            subtype=(str(item.get("subtype")).strip() or None) if item.get("subtype") else None,
            occurred_at=occurred_at, date_precision=precision, evidence_quote=evidence_quote))

    edges, edge_rejections = _validate_edges(data.get("edges") or [], known=known, window_text=window_text)
    result.edges = edges
    result.rejected.extend(edge_rejections)

    if len(result.atoms) >= MAX_ATOMS_PER_WINDOW:
        raise WindowTruncated(
            f"окно дало {len(result.atoms)} атомов при потолке {MAX_ATOMS_PER_WINDOW}")
    return result


def extract_window(window_text: str, *, domain: str, heading_path: tuple[str, ...] = (),
                   model: str = DEFAULT_MODEL, keep_alive: str | None = None,
                   attempts: int = MAX_REPAIR_ATTEMPTS) -> WindowExtraction:
    """Разобрать одно окно с ограниченным числом попыток починки.

    `WindowTruncated` НЕ ловится: это не сбой модели, а сигнал звену
    выше поделить окно. Попытка «починить» переполнение повтором дала бы
    ровно тот же результат, только медленнее.

    `keep_alive=None` (по умолчанию) означает «взять production policy из
    Settings» (R4, §14.18) — параметр существует явно, чтобы бенчмарк R4
    мог перебирать политику, не трогая Settings процесса.
    """
    complaint: str | None = None
    for attempt in range(1, attempts + 1):
        prompt = _prompt(window_text, domain=domain, heading_path=heading_path,
                         complaint=complaint)
        try:
            return validate(_call_ollama(prompt, model=model, keep_alive=keep_alive),
                            window_text=window_text)
        except WindowTruncated:
            raise
        except ExtractionFailed as exc:
            complaint = str(exc)
            logger.warning("окно не разобрано, попытка %d из %d: %s", attempt, attempts, exc)
    raise ExtractionFailed(f"не удалось разобрать окно за {attempts} попыток: {complaint}")


#: P2 (владелец 2026-09-04) — на сколько уровней вглубь можно делить окно
#: на transport timeout, прежде чем сдаться (не бесконечная рекурсия на
#: патологическом единственном неразделимом предложении).
MAX_SPLIT_DEPTH = 3

#: Сколько символов хвоста предыдущего куска переносить в начало следующего
#: при разбиении по предложениям — минимальный контекст для анафоры/
#: продолжения мысли (P2: «минимальный overlap, если он нужен для
#: сохранения соседнего контекста»). Только для sentence-split: абзацы
#: обычно уже самодостаточные единицы контекста.
SENTENCE_SPLIT_OVERLAP_CHARS = 80

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ«\"“])")


def _split_paragraphs(text: str) -> list[str] | None:
    """Границы абзацев — первый, самый дешёвый уровень деления (P2: «paragraph
    boundary first»). `None`, если делить некуда (один абзац)."""
    parts = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]
    return parts if len(parts) > 1 else None


def _split_sentences(text: str) -> list[str] | None:
    """Второй уровень (P2: «sentence boundary second»), с overlap-хвостом
    предыдущего предложения — без него ссылка вида «Из-за этого...» в
    начале куска теряет контекст, на который ссылается."""
    parts = [p.strip() for p in _SENTENCE_BOUNDARY_RE.split(text) if p.strip()]
    if len(parts) <= 1:
        return None
    pieces = [parts[0]]
    for prev, cur in zip(parts, parts[1:]):
        # Хвост СТРОГО короче prev (не вся prev целиком) — иначе для
        # короткого предыдущего предложения кусок с overlap воспроизводит
        # исходный текст побайтово, и деление на timeout зацикливается на
        # той же строке вместо прогресса.
        overlap_len = min(SENTENCE_SPLIT_OVERLAP_CHARS, max(len(prev) - 1, 0))
        tail = prev[-overlap_len:] if overlap_len else ""
        pieces.append(f"{tail} {cur}" if tail else cur)
    return pieces


def _merge_node_extractions(parts: list[WindowExtraction]) -> WindowExtraction:
    """Склеить результаты дочерних кусков в один `WindowExtraction` для
    родительского окна. local_id из разных кусков — разные пространства
    имён (модель нумерует их независимо в каждом вызове), поэтому
    переименовываем с префиксом куска, а не полагаемся на совпадение.
    Кто с кем одно лицо между кусками (в т.ч. дубликаты из overlap) —
    решает разрешение сущностей (R6), не эта функция."""
    merged = WindowExtraction()
    for i, part in enumerate(parts):
        prefix = f"p{i}_"
        for e in part.entities:
            merged.entities.append(replace(e, local_id=prefix + e.local_id))
        for a in part.atoms:
            merged.atoms.append(replace(a, local_id=prefix + a.local_id))
        merged.rejected.extend(part.rejected)
    return merged


def _validate_nodes(raw: str, *, window_text: str) -> WindowExtraction:
    """Разбор ответа node-only схемы (`NODE_RESPONSE_SCHEMA`, без edges).

    Владелец, R4 EXIT FIX (2026-09-04), после run 247: `_extract_nodes_once()`
    раньше делегировала парсинг общей `validate()`, у которой ОДНО
    пространство `local_id` на entities+atoms вместе — обязательный
    контракт старой edge-схемы (ребро адресует любой из двух типов по
    local_id, значит оба обязаны делить один реестр имён). Node-only-путь
    edges не просит и не производит (P1) — `NODE_SYSTEM_PROMPT` не
    требует от модели единого счётчика, и модель, как любая LLM без такой
    инструкции, нумерует entities и atoms НЕЗАВИСИМО с 1. Общий `known` из
    `validate()` топил как «дубликат» почти каждый атом, чей raw local_id
    совпал с local_id уже виденной сущности (run 247: 29 ложных
    отклонений на 21 кейс, critical_entity_event_recall 58.3%).

    Раздельные множества `known_entities`/`known_atoms` + канонический
    префикс типа (`e:`/`a:`) снимают эту ложную коллизию, сохраняя дедуп
    настоящих повторов (два entity или два atom с одним raw id внутри
    одного списка — по-прежнему reject). Ни один даунстрим (relation_compiler.py,
    _merge_node_extractions) не сравнивает local_id entity с local_id
    atom — каждый читает local_id как непрозрачную строку своего же
    списка, так что канонизация с префиксом ничего не ломает.

    `validate()`/`extract_window()` НЕ трогаются — ими продолжают
    пользоваться historical/experimental edge-aware пути
    (`semantic_extract_c2.py`, `semantic_extract_twopass.py`)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionFailed(f"невалидный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractionFailed(f"ожидался объект, пришёл {type(data).__name__}")

    result = WindowExtraction()
    known_entities: set[str] = set()
    known_atoms: set[str] = set()

    for item in data.get("entities") or []:
        if not isinstance(item, dict):
            result.rejected.append("сущность не объект")
            continue
        raw_id = str(item.get("local_id") or "").strip()
        label = str(item.get("label") or "").strip()
        entity_type = str(item.get("entity_type") or "").strip().upper()
        if not raw_id or not label or not entity_type:
            result.rejected.append(f"сущность без обязательного поля: {item!r:.80}")
            continue
        if raw_id in known_entities:
            result.rejected.append(f"повтор local_id {raw_id!r} (entity)")
            continue
        evidence_quote = str(item.get("evidence_quote") or "").strip()
        if not evidence_quote:
            result.rejected.append(f"сущность без evidence_quote: {item!r:.80}")
            continue
        if not _evidence_grounded(evidence_quote, window_text):
            result.rejected.append(f"evidence_quote сущности не найден в тексте окна: {evidence_quote!r:.80}")
            continue
        known_entities.add(raw_id)
        aliases = tuple(str(a).strip() for a in item.get("aliases") or [] if str(a).strip())
        result.entities.append(ExtractedEntity(
            local_id=f"e:{raw_id}", entity_type=entity_type, label=label,
            subtype=(str(item.get("subtype")).strip() or None) if item.get("subtype") else None,
            aliases=aliases, evidence_quote=evidence_quote))

    for item in data.get("atoms") or []:
        if not isinstance(item, dict):
            result.rejected.append("атом не объект")
            continue
        raw_id = str(item.get("local_id") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "").strip()
        if not raw_id or not title or not text:
            result.rejected.append(f"атом без обязательного поля: {item!r:.80}")
            continue
        if kind not in _ATOM_KINDS:
            result.rejected.append(f"вид {kind!r} вне реестра")
            continue
        if raw_id in known_atoms:
            result.rejected.append(f"повтор local_id {raw_id!r} (atom)")
            continue
        evidence_quote = str(item.get("evidence_quote") or "").strip()
        if not evidence_quote:
            result.rejected.append(f"атом без evidence_quote: {item!r:.80}")
            continue
        if not _evidence_grounded(evidence_quote, window_text):
            result.rejected.append(f"evidence_quote атома не найден в тексте окна: {evidence_quote!r:.80}")
            continue
        if _NEGATION_RE.search(evidence_quote) and not _NEGATION_RE.search(text):
            result.rejected.append(f"отрицание есть в evidence, но потеряно в тексте атома: {text!r:.80}")
            continue

        precision = str(item.get("date_precision") or "").strip().lower() or None
        if precision and precision not in _DATE_PRECISIONS:
            # Точность вне реестра — не повод терять весь атом: сама
            # дата остаётся, точность становится «неизвестна».
            result.rejected.append(f"точность даты {precision!r} вне реестра")
            precision = SemanticDatePrecision.UNKNOWN.value
        occurred_at = (str(item.get("occurred_at")).strip() or None) if item.get("occurred_at") else None

        if occurred_at and _RELATIVE_DATE_MARKERS_RE.search(evidence_quote):
            result.rejected.append(
                f"evidence описывает относительную дату, но occurred_at выставлен: {occurred_at!r}")
            continue
        if occurred_at and precision in _PRECISE_DATE_PRECISIONS and not _ABSOLUTE_DATE_RE.search(evidence_quote):
            result.rejected.append(
                f"occurred_at {occurred_at!r} не подтверждён абсолютной датой в evidence: "
                f"{evidence_quote!r:.80}")
            continue

        known_atoms.add(raw_id)
        result.atoms.append(ExtractedAtom(
            local_id=f"a:{raw_id}", kind=kind, title=title, text=text,
            subtype=(str(item.get("subtype")).strip() or None) if item.get("subtype") else None,
            occurred_at=occurred_at, date_precision=precision, evidence_quote=evidence_quote))

    if len(result.atoms) >= MAX_ATOMS_PER_WINDOW:
        raise WindowTruncated(
            f"окно дало {len(result.atoms)} атомов при потолке {MAX_ATOMS_PER_WINDOW}")
    return result


def _extract_nodes_once(window_text: str, *, domain: str, heading_path: tuple[str, ...],
                        model: str, keep_alive: str | None, attempts: int) -> WindowExtraction:
    """Один узел рекурсии `extract_nodes_window()`: repair-retry (P2:
    «сохранить для malformed JSON, schema failure») на месте, без деления
    текста. Transport timeout — не чинится повтором того же текста, сразу
    поднимается `ExtractionTimedOut` вызывающему для деления."""
    complaint: str | None = None
    for attempt in range(1, attempts + 1):
        prompt = _prompt(window_text, domain=domain, heading_path=heading_path,
                         complaint=complaint)
        try:
            raw = _call_ollama(prompt, model=model, keep_alive=keep_alive,
                               system=NODE_SYSTEM_PROMPT, response_schema=NODE_RESPONSE_SCHEMA)
        except ExtractionFailed as exc:
            if isinstance(exc.__cause__, TimeoutError):
                raise ExtractionTimedOut(str(exc)) from exc
            complaint = str(exc)
            logger.warning("окно не разобрано (node-only), попытка %d из %d: %s",
                           attempt, attempts, exc)
            continue
        try:
            return _validate_nodes(raw, window_text=window_text)
        except WindowTruncated:
            raise
        except ExtractionFailed as exc:
            complaint = str(exc)
            logger.warning("окно не разобрано (node-only), попытка %d из %d: %s",
                           attempt, attempts, exc)
    raise ExtractionFailed(f"не удалось разобрать окно за {attempts} попыток: {complaint}")


def extract_nodes_window(window_text: str, *, domain: str, heading_path: tuple[str, ...] = (),
                         model: str = DEFAULT_MODEL, keep_alive: str | None = None,
                         attempts: int = MAX_REPAIR_ATTEMPTS, _depth: int = 0,
                         _lineage: list[dict] | None = None) -> WindowExtraction:
    """P1/P2 (владелец 2026-09-04) — production/final-acceptance путь:
    просит модель только entities+atoms (edges строит исключительно
    `relation_compiler.py` — см. `NODE_RESPONSE_SCHEMA`), и на transport
    timeout делит окно детерминированно (paragraph boundary → sentence
    boundary, `MAX_SPLIT_DEPTH` уровней) вместо identical retry (R4 RCA
    run 241: 3×120с подряд на `long_dense_window` не дали новой
    информации). Результаты кусков склеиваются `_merge_node_extractions()`.
    Не заменяет `extract_window()` — тот остаётся нетронутым для
    historical/experimental путей.

    `WindowTruncated` не ловится по тем же причинам, что у `extract_window()`.
    Если делить уже некуда (один неразделимый абзац из одного предложения)
    или глубина исчерпана — исключение уходит наверх: coverage contract
    требует явный провал, не тихую потерю содержимого.

    `_lineage` (P7, владелец 2026-09-04) — необязательный out-параметр:
    если передан список, в него добавляются записи о каждом узле рекурсии
    (глубина/длина/исход), БЕЗ единого символа самого текста окна — это
    метаданные разбиения, не содержимое. Используется только
    `run_golden_benchmark()` для synthetic-only diagnostics (P7);
    `run_shadow_benchmark()` этот параметр не передаёт вовсе."""
    try:
        result = _extract_nodes_once(window_text, domain=domain, heading_path=heading_path,
                                     model=model, keep_alive=keep_alive, attempts=attempts)
        if _lineage is not None:
            _lineage.append({"depth": _depth, "window_chars": len(window_text), "outcome": "ok"})
        return result
    except ExtractionTimedOut:
        if _depth >= MAX_SPLIT_DEPTH:
            if _lineage is not None:
                _lineage.append({"depth": _depth, "window_chars": len(window_text),
                                 "outcome": "timeout_depth_exhausted"})
            raise
        pieces = _split_paragraphs(window_text) or _split_sentences(window_text)
        if pieces is None:
            if _lineage is not None:
                _lineage.append({"depth": _depth, "window_chars": len(window_text),
                                 "outcome": "timeout_unsplittable"})
            raise
        if _lineage is not None:
            _lineage.append({"depth": _depth, "window_chars": len(window_text),
                             "outcome": "timeout_split", "pieces": len(pieces)})
        parts = [extract_nodes_window(piece, domain=domain, heading_path=heading_path,
                                      model=model, keep_alive=keep_alive, attempts=attempts,
                                      _depth=_depth + 1, _lineage=_lineage)
                for piece in pieces]
        return _merge_node_extractions(parts)
