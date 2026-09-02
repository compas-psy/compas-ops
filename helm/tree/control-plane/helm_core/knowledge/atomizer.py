"""Semantic atomizer — L1 SOURCE → L2 KNOWLEDGE notes (ADR-019, §14.1/§14.3).

Домено-агностично по конструкции: этот модуль не содержит ни одной ветки
"если domain == health" или похожей — маршрутизация записи (public vs
health-схема) делегирована ТЕМ ЖЕ ФУНКЦИЯМ (`is_health_domain()`/
`health_schema_configured()`/`health_schema.write_notes()`), что уже
используются `relations.py`/`ingest.py`/`worker.py` для чанков и связей.
Health не более "особый" домен для атомизатора, чем `ventures` или
`simpas/company` — просто единственный, для которого сегодня прогнан
`scripts/setup-health-role.sh` и включена отдельная Postgres-роль
(решение владельца при разборе P12, ADR-005), а не решение этого модуля.

Один вызов Ollama на один L1 SOURCE текст — извлекает несколько маленьких
смысловых атомов (НЕ технический chunk: один факт/сущность/событие/
решение на атом), каждый со своим `slug` и списком `links` на другие
атомы. Рендерится в Markdown с `[[wikilink]]`-синтаксисом, который
existing `relations.py::store_relations()` подхватывает БЕЗ ИЗМЕНЕНИЙ —
он уже вызывается на каждом ingest (`ingest.py`/`worker.py`).

Контролируемый словарь типов — сознательно ýже, чем в самом ADR-019:
`DOCUMENT` не нужен (это уже L1 SOURCE, отдельная сущность), `DATE` —
атрибут события/факта, а не отдельный узел графа (в Obsidian не заводят
заметку на каждую дату) — оба выброшены здесь, не в ADR, как находка при
реализации, не пересмотр архитектуры.

Модель — `gemma2:2b`, ТА ЖЕ, что уже поднята для Z2-рефраза
(`rephrase.py`, `docs/KNOWLEDGE_MODELS.md`) — не отдельный замер, а
временный выбор "не тащить вторую модель на сервер с бюджетом ~1.5 ГБ
запаса до реального замера" (ADR-019, шаг 4 плана: конкретное средство
атомизации выбирается по факту качества на реальном корпусе, не
заранее). `OLLAMA_KEEP_ALIVE=0` — тот же принцип, что и у Z2/GigaAM:
воркер вызывает атомизатор в фоновой задаче (не в request path
владельца), холодная латентность здесь дешевле, чем держать модель
резидентной.

Fail-open: недоступность Ollama или невалидный JSON не должны ронять
ingest — `atomize_or_empty()` возвращает `[]`, source/chunks/relations
(слой 1) создаются как раньше, просто без L2-слоя (та же деградация,
что `embeddings.py::embed_texts_or_none()`/`rephrase.py::
rephrase_or_none()`).

Известное ограничение, не решаемое здесь: entity resolution между
атомизациями РАЗНЫХ источников — по конструкции (Obsidian wikilink
resolution по точному совпадению текста slug), не по эвристикам/fuzzy
match. Один и тот же врач, названный в одном документе "Иванов", а в
другом "врач Иванов А.С.", сегодня станет ДВУМЯ заметками. Осознанно не
исправляется сейчас (ADR-019 §"Что сознательно не входит") — совпадение
slug'ов из одного источника уже мёржится (см. `store_notes()`).
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .health_schema import health_schema_configured, is_health_domain, write_notes
from .relations import store_relations
from ..models import KnowledgeNote

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://ollama:11434/api/generate"
MODEL_NAME = "gemma2:2b"
REQUEST_TIMEOUT = 60
KEEP_ALIVE = "0"

#: Контролируемый словарь — конвенция атомизатора, не ограничение схемы
#: (`KnowledgeNote.type` — свободный `String(32)`, менять нечего).
NOTE_TYPES = frozenset({
    "CONCEPT", "FACT", "ENTITY", "PERSON", "ORGANIZATION", "PLACE", "EVENT", "DECISION",
})

#: Куда физически ложится .md-файл атома — общие для ВСЕХ доменов
#: каталоги Vault (`scripts/knowledge-bootstrap.sh`, §14.2), без
#: под-каталога по домену: domain — метаданные заметки (frontmatter), не
#: ось файловой раскладки, у самого Vault такой оси для L2-заметок нет.
_TYPE_DIR = {
    "CONCEPT": "concepts", "FACT": "concepts",
    "ENTITY": "entities", "PERSON": "entities", "ORGANIZATION": "entities", "PLACE": "entities",
    "EVENT": "meetings",
    "DECISION": "decisions",
}

#: Защита от патологического ответа модели (зацикленный JSON, галлюцинация
#: сотен атомов на один абзац) — тот же принцип осторожности, что и у
#: per-user quotas (§14.4), не новая инфраструктура ради этого файла.
#: 60, а не 20: живой замер 02.09.2026 на реальной консультации
#: эндокринолога (4000 символов) дал 40 осмысленных атомов — прежний
#: предел резал ровно ту половину, где назначения и рекомендации.
MAX_ATOMS_PER_CALL = 60

#: Форма ответа задаётся СХЕМОЙ, а не просьбой в тексте промпта. Живой
#: замер 02.09.2026 (`scripts/atomizer-prompt-lab.sh`, один и тот же
#: документ, один и тот же промпт): с `format: "json"` gemma2:2b вернула
#: ОДИН объект — Ollama гарантирует валидный JSON, но не его форму; с
#: этой схемой — 40 атомов, включая «Врач эндокринолог: Бокова Мария
#: Николаевна» (PERSON) и «Приём врача-эндокринолога повторный» (EVENT),
#: то есть ровно то, чего не находил чанковый поиск.
_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "type": {"type": "string", "enum": sorted(NOTE_TYPES)},
            "text": {"type": "string"},
            "links": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["slug", "type", "text"],
    },
}

#: Модель видит только начало длинного текста — контекстное окно gemma2:2b
#: на CPU не резиновое, и это временный выбор модели (см. докстринг),
#: не окончательное архитектурное ограничение.
MAX_INPUT_CHARS = 4000

_SYSTEM_PROMPT = (
    "Ты — детерминированный извлекатель структурированных знаний из личного "
    "архива владельца (любая сфера жизни: здоровье, работа, покупки, "
    "обучение, встречи, проекты). Разбей текст на маленькие смысловые "
    "единицы — конкретные факты, сущности (люди/организации/места), "
    "события, решения — упомянутые буквально в тексте. Не добавляй того, "
    "чего нет в тексте, не оценивай и не советуй.\n\n"
    "Ответ — МАССИВ объектов, по одному на каждую найденную единицу. "
    "Без пояснений до или после, без markdown-разметки вокруг.\n\n"
    "Допустимые значения type: ENTITY, PERSON, ORGANIZATION, PLACE, EVENT, "
    "CONCEPT, FACT, DECISION.\n\n"
    "Пример для текста «12 марта был на приёме у Петрова, кардиолога, "
    "в клинике Здоровье»:\n"
    '[{"slug": "Петров", "type": "PERSON", '
    '"text": "Кардиолог, вёл приём 12 марта.", '
    '"links": ["кардиолог", "клиника Здоровье"]},\n'
    ' {"slug": "приём кардиолога", "type": "EVENT", '
    '"text": "Приём 12 марта у кардиолога Петрова.", '
    '"links": ["Петров"]},\n'
    ' {"slug": "клиника Здоровье", "type": "ORGANIZATION", '
    '"text": "Клиника, где вёлся приём.", "links": []}]\n\n'
    "В links пиши имена других найденных единиц ровно так, как они стоят "
    "в поле slug — ничего больше. Если зацепиться не за что — верни []."
)

_FORBIDDEN_SLUG_CHARS = re.compile(r'[/\\:*?"<>|\r\n\t]')
_WHITESPACE = re.compile(r"\s+")


class AtomizerUnavailable(RuntimeError):
    """Ollama недоступна, ответила ошибкой, или вернула невалидный JSON."""


@dataclass(frozen=True)
class AtomizedAtom:
    slug: str
    type: str
    text: str
    links: tuple[str, ...]


def _slugify(raw: str) -> str:
    value = _WHITESPACE.sub(" ", _FORBIDDEN_SLUG_CHARS.sub("", raw)).strip()
    return value[:128]


def _parse_atoms(raw_json: str) -> list[AtomizedAtom]:
    data = json.loads(raw_json)
    # НАЙДЕНО живьём 02.09.2026 на реальных документах владельца: gemma2:2b
    # в режиме format=json возвращает ОДИН объект вместо массива (Ollama
    # гарантирует валидный JSON, но не форму верхнего уровня). Реально
    # извлечённая сущность при этом корректна — выбрасывать её из-за формы
    # обёртки нельзя. Принимаем и одиночный объект, и массив, завёрнутый в
    # объект под произвольным ключом.
    if isinstance(data, dict):
        nested = next((v for v in data.values() if isinstance(v, list)), None)
        data = nested if nested is not None else [data]
    if not isinstance(data, list):
        raise AtomizerUnavailable("ответ модели — не JSON-массив и не объект")

    atoms: list[AtomizedAtom] = []
    for item in data[:MAX_ATOMS_PER_CALL]:
        if not isinstance(item, dict):
            continue
        slug = _slugify(str(item.get("slug") or ""))
        note_type = str(item.get("type") or "").strip().upper()
        text = str(item.get("text") or "").strip()
        # Та же дисциплина, что extract_frontmatter_relations(): запись без
        # обязательного поля пропускается целиком, не додумывается.
        if not slug or note_type not in NOTE_TYPES or not text:
            continue
        links = tuple(_slugify(str(link)) for link in item.get("links") or [] if str(link).strip())
        atoms.append(AtomizedAtom(slug=slug, type=note_type, text=text, links=tuple(l for l in links if l)))
    return atoms


def atomize(text: str, *, domain: str) -> list[AtomizedAtom]:
    """Поднимает `AtomizerUnavailable` при сбое — вызывающая сторона решает,
    деградировать на "без L2-слоя" (fail-open) или нет. `domain` уходит в
    промпт только как контекст для модели, не меняет логику извлечения —
    один и тот же промпт для всех доменов (домено-агностичность)."""
    prompt = (
        f"Домен: {domain}\n\nТекст:\n{text[:MAX_INPUT_CHARS]}"
    )
    body = {
        "model": MODEL_NAME,
        "system": _SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "format": _RESPONSE_SCHEMA,
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            result = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise AtomizerUnavailable(str(exc)) from exc

    raw_response = (result.get("response") or "").strip()
    if not raw_response:
        raise AtomizerUnavailable("ollama вернула пустой ответ")
    try:
        atoms = _parse_atoms(raw_response)
        if not atoms:
            # "Модель ответила, но всё отсеялось фильтром" и "модель не
            # ответила" — разные вещи, и в fail-open логе они обязаны
            # различаться: без этого ноль атомов необъясним (найдено
            # живьём 02.09.2026 — молчаливый ноль на всех трёх источниках).
            logger.warning("атомизатор вернул 0 атомов, сырой ответ модели: %r",
                           raw_response[:300])
        return atoms
    except (json.JSONDecodeError, AtomizerUnavailable) as exc:
        # Сырой ответ модели — не только текст исключения — обязателен в
        # сообщении: без него "невалидный JSON" ничем не отличается от
        # "модель недоступна" в логе fail-open, диагностировать нечего.
        raise AtomizerUnavailable(
            f"невалидный JSON от модели: {exc} | сырой ответ: {raw_response[:300]!r}") from exc


def atomize_or_empty(text: str, *, domain: str) -> list[AtomizedAtom]:
    """Fail-open обёртка для `ingest.py`/`worker.py` — недоступность
    атомизатора не должна ронять ingest, L1 SOURCE/chunks/слой-1-relations
    создаются как раньше, просто без L2-слоя."""
    try:
        return atomize(text, domain=domain)
    except AtomizerUnavailable as exc:
        logger.warning("semantic atomizer недоступен, L2-слой пропущен: %s", exc)
        return []


def _render_body(atom: AtomizedAtom) -> str:
    lines = [atom.text]
    if atom.links:
        lines += ["", "Связано: " + ", ".join(f"[[{link}]]" for link in atom.links)]
    return "\n".join(lines)


def _render_frontmatter(*, slug: str, note_type: str, domain: str) -> str:
    return f"---\nid: {slug}\ntype: {note_type}\ndomain: {domain}\n---\n\n"


def _write_note_file(*, file_path: str, atom: AtomizedAtom, domain: str, is_new: bool) -> None:
    body = _render_body(atom)
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_new:
        path.write_text(_render_frontmatter(slug=atom.slug, note_type=atom.type, domain=domain) + body,
                        encoding="utf-8")
    else:
        # Заметка уже существует (тот же slug пришёл из другого source) —
        # растущая заметка, не перезапись: новый вклад дописывается, старое
        # содержимое не теряется (тот же Obsidian-принцип, что и у
        # накопительной заметки, которая обрастает вечера за вечером).
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n---\n\n{body}")


def _note_file_path(*, vault_root: str, atom: AtomizedAtom) -> str:
    """`vault_root` здесь — корень ДОМЕНА, не общий Vault.

    §14.16 F15: маршрутизация строки в health-схему не отменяет того, что
    сам .md-файл ложился в общий `<vault>/entities/`. Развилку считает
    вызывающий (`ingest.py`/`worker.py` через `vault.scope_root()`), сюда
    приходит уже готовый корень — чтобы не появилось второе место, где
    решается, куда писать health."""
    return f"{vault_root}/{_TYPE_DIR[atom.type]}/{atom.slug}.md"


def store_notes(session: Session, *, domain: str, knowledge_user_id: uuid.UUID | None,
                source_id: uuid.UUID, source_sha256: str, atoms: list[AtomizedAtom],
                vault_root: str) -> int:
    """Записать атомы как L2 `KnowledgeNote` + relations слоя 1 (через уже
    существующий `store_relations()`, без изменений в нём). Идемпотентно
    по slug: повторный атом с тем же `(knowledge_user_id, slug)` дополняет
    существующую заметку (`source_ids`/файл на диске растут), не дублирует
    строку — иначе `UniqueConstraint(knowledge_user_id, slug)` упал бы уже
    на втором документе про того же человека/сущность."""
    if not atoms:
        return 0

    use_health = is_health_domain(domain) and health_schema_configured()
    stored = 0
    for atom in atoms:
        file_path = _note_file_path(vault_root=vault_root, atom=atom)

        if use_health:
            is_new = write_notes(
                knowledge_user_id=knowledge_user_id, source_id=source_id,
                source_sha256=source_sha256, slug=atom.slug, note_type=atom.type,
                domain=domain, file_path=file_path,
            )
        else:
            existing = session.scalar(
                select(KnowledgeNote).where(
                    KnowledgeNote.knowledge_user_id == knowledge_user_id,
                    KnowledgeNote.slug == atom.slug,
                )
            )
            if existing is None:
                session.add(KnowledgeNote(
                    knowledge_user_id=knowledge_user_id, slug=atom.slug, type=atom.type,
                    domain=domain, file_path=file_path,
                    source_ids=[str(source_id)], source_sha256=[source_sha256],
                ))
                is_new = True
            else:
                if str(source_id) not in (existing.source_ids or []):
                    existing.source_ids = [*(existing.source_ids or []), str(source_id)]
                if source_sha256 not in (existing.source_sha256 or []):
                    existing.source_sha256 = [*(existing.source_sha256 or []), source_sha256]
                is_new = False

        _write_note_file(file_path=file_path, atom=atom, domain=domain, is_new=is_new)
        store_relations(session, domain=domain, knowledge_user_id=knowledge_user_id,
                        from_id=atom.slug, source_id=source_id, text=_render_body(atom))
        stored += 1
    return stored


def atomize_and_store(session: Session, *, domain: str, knowledge_user_id: uuid.UUID | None,
                      source_id: uuid.UUID, source_sha256: str, text: str,
                      vault_root: str) -> int:
    """Точка входа для `ingest.py`/`worker.py`. На время rescue не пишет
    ничего и возвращает 0.

    v4.0, шаг R2: «Legacy semantic-v1 remains quarantined/read-only
    during migration». Причина не в том, что слой v1 плох в целом, а в
    двух конкретных вещах, названных спекой нарушениями прямо:

    - `store_notes()` ниже дописывает текст второго источника в заметку
      первого по совпадению `slug` — §14.23 «merging all text about same
      slug into one growing entity file», §14.6 «forbidden for v4 source
      facts/events»: так теряется происхождение утверждения и склеиваются
      однофамильцы;
    - связи, порождённые моделью, уезжают в `knowledge_relations` с
      `evidence_type = explicit_link` — §14.23 «labeling model-generated
      links OWNER_EXPLICIT/explicit_link as if owner wrote them».

    Пока это работало на каждом ingest, корпус v1 продолжал расти именно
    тем способом, который R3 обязан заменить. Замораживается здесь, в
    одной точке, а не удалением вызовов из `ingest.py`/`worker.py`:
    писателя графа v2 ещё нет, и место, куда он встанет, должно остаться
    там же, где было.

    Что при этом НЕ выключено: слой 1 (`store_relations()` по
    wikilink'ам, написанным владельцем), чанки, эмбеддинги, поиск. Ни
    один ответ сегодня не читает `knowledge_notes` — заморозка не
    отнимает у владельца ни одного ответа, только перестаёт копить
    недоверенные данные и зря звать модель на каждом ingest.

    Снимается в R3 вместе с заменой контракта вывода (§14.24
    «Refactor/replace: atomizer.py output contract, KnowledgeNote
    merge-by-slug semantics»).
    """
    logger.debug("semantic-v1 заморожен на время rescue (R2), источник %s не атомизируется",
                 source_id)
    return 0
