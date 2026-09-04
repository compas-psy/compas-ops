"""R4.7 (владелец 03.09.2026) — deterministic relation compiler.

«R4.6 research CLOSED»: NLI-модели, chat-LLM relation experiments,
prompt/verbalizer tuning, cloud relation extraction — запрещены без
нового owner decision (`docs/KNOWLEDGE_MODELS.md`, R4.6.F). Принятая
production-архитектура:

    local semantic extractor → nodes/atoms
    deterministic relation compiler → trusted edges

Этот модуль — вторая стрелка. Он НЕ вызывает LLM и не смотрит, что
модель сама предложила в `edges` (`ExtractedEdge`, `semantic_extract.py`)
— то поле полностью игнорируется production-путём начиная с R4.7,
рёбра порождает только код ниже, детерминированно, из уже провалидированных
`entities`/`atoms` (`_evidence_grounded()` в `semantic_extract.py` уже
отбросил всё, чей `evidence_quote` не дословная подстрока окна — этот
модуль доверяет их evidence, не проверяет её заново).

Контракт на каждый auto-extractable тип (владелец, дословно):
    deterministic rule + explicit supporting evidence + endpoint
    grounding + direction contract + provenance + tests.
«Если rule не доказывает edge — edge не создаётся.» `RELATED_TO`
запрещён как fallback: неизвестная/сомнительная связь — просто НЕ
рождается, не понижается ни во что.

`AUTO_EXTRACTABLE_RELATIONS_V1` (`models/base.py`) — 8 типов:
INVOLVES, HAS_ROLE, ABOUT, LOCATED_AT, REASON_FOR, RESULTED_IN,
SUPPORTS — все реализованы здесь, на тексте окна. DERIVED_FROM —
ИСКЛЮЧЕНИЕ: владелец прямо запретил отдавать эту связь модели или
любой текстовой эвристике («semantic node → source/document, строить
полностью детерминированно из provenance ingest pipeline») — она
рождается из `KnowledgeNodeMention.source_id`/будущего `DOCUMENT_REF`
узла на этапе публикации в граф, не здесь. `compile_relations()` НИКОГДА
не возвращает `derived_from` — см. `derive_from_source()` ниже
(отдельная, чисто структурная функция без единого вызова на тексте).

Почему нет одной функции на семь типов: каждый тип разрешён только для
конкретной пары категорий узлов (ATOM×ENTITY или ENTITY×ENTITY, с
конкретными допустимыми kind/entity_type), и категории НЕ пересекаются
между типами — это специально спроектированное разбиение (см.
docstring каждой `_compile_*`), устраняющее коллизии «одна и та же пара
подходит под два типа одновременно» ценой recall на пограничных случаях
(например `located_at` не порождается для ORGANIZATION-целей —
только для PLACE, чтобы не конфликтовать с `involves`, для которого
ORGANIZATION — законная цель). Recall — диагностика, не цель; owner
явно запретил «пытаться добиться recall ценой inference».
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..models.base import SemanticRelationType
from .semantic_extract import ExtractedAtom, ExtractedEdge, ExtractedEntity

_TOKEN_RE = re.compile(r"[А-ЯЁа-яёA-Za-z0-9]+")


def _stem(word: str) -> str:
    """Лёгкий детерминированный стемминг: русское словоизменение почти
    всегда меняет только последние 2-4 символа (падежные окончания) —
    `entity.label` нормализован (именительный падеж), а evidence несёт
    исходный текст в падеже, требуемом грамматикой («Гавриловой Марины
    Сергеевны» вместо «Гаврилова Марина Сергеевна»). Не морфологический
    анализ — фиксированное усечение хвоста, проверено вручную на всех
    склонениях, встречающихся в golden/v3 fixtures."""
    w = word.lower()
    if len(w) <= 4:
        return w
    return w[: max(4, len(w) - 3)]


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _label_words(label: str) -> list[str]:
    return [w for w in _TOKEN_RE.findall(label) if len(w) > 1]


def _label_words_all(label: str) -> list[str]:
    """Как `_label_words`, но БЕЗ фильтра длины — нужен там, где важна
    последовательность позиций токенов (`_token_span`): однобуквенный
    предлог внутри label («дача В посёлке Сосновка») в `_label_words`
    отфильтрован (не нужен для независимой AND-проверки в `is_mentioned`),
    но в evidence он остаётся отдельным токеном и разрывает последовательность
    индексов — без него `_token_span` не находит фразу целиком, хотя она
    дословно присутствует (найдено офлайн-сверкой с v3 `v3_family_property`)."""
    return _TOKEN_RE.findall(label)


def is_mentioned(text: str, label: str, aliases: Sequence[str] = ()) -> bool:
    """Endpoint grounding: каждое (стеммированное) слово `label` или
    одного из `aliases` встречается как префикс какого-то токена
    `text` — не «где-то в том же абзаце», а буквально в этом evidence."""
    tokens = _tokens(text)
    for candidate in (label, *aliases):
        words = _label_words(candidate)
        if not words:
            continue
        stems = [_stem(w) for w in words]
        if all(any(tok.startswith(stem) for tok in tokens) for stem in stems):
            return True
    return False


def _first_mention_index(text: str, label: str) -> int:
    """Позиция первого слова `label` (по стему) в `text`, для проверки
    близости ролевого маркера — не индекс символа исходной строки, а
    порядковый номер токена (proximity меряется в токенах, не символах:
    надёжнее при разной длине склонённых форм)."""
    tokens = _tokens(text)
    words = _label_words(label)
    if not words:
        return -1
    first_stem = _stem(words[0])
    for i, tok in enumerate(tokens):
        if tok.startswith(first_stem):
            return i
    return -1


def _token_span(text: str, label: str) -> tuple[int, int] | None:
    """(первый, последний) индекс токенов, покрывающих все слова label
    подряд в `text` (по стему), или `None`, если не найдено целиком."""
    tokens = _tokens(text)
    words = _label_words_all(label)
    if not words:
        return None
    stems = [_stem(w) for w in words]
    n = len(stems)
    for start in range(len(tokens) - n + 1):
        if all(tokens[start + i].startswith(stems[i]) for i in range(n)):
            return start, start + n - 1
    return None


# ---------------------------------------------------------------------------
# INVOLVES — ATOM(event|fact|decision) → ENTITY(PERSON|ORGANIZATION).
# PLACE исключён намеренно: не встречается в golden/v3 gold ни разу, и
# семантически участник события — не место. Endpoint grounding: label
# сущности (или алиас) встречается в evidence САМОГО атома.
# ---------------------------------------------------------------------------

_INVOLVES_ATOM_KINDS = frozenset({"event", "fact", "decision"})
_INVOLVES_ENTITY_TYPES = frozenset({"PERSON", "ORGANIZATION"})

#: R7 doctor path (владелец, дословный контракт): `EVENT
#: --INVOLVES(role=doctor)--> PERSON` — только если «врач»/«врача»/
#: «врачом»/«врач-специальность» встречается НЕПОСРЕДСТВЕННО перед
#: именем человека в evidence, не где-то в тексте вообще.
_DOCTOR_MARKER_RE = re.compile(r"врач[а-яё]*(?:-[а-яё]+)?", re.IGNORECASE)
_ROLE_PROXIMITY_TOKENS = 3


def _atom_evidence(atom: ExtractedAtom) -> str:
    return atom.evidence_quote or atom.text


def _entity_names(entity: ExtractedEntity) -> tuple[str, ...]:
    return (entity.label, *entity.aliases)


def _duplicate_label_keys(entities: Sequence[ExtractedEntity]) -> frozenset[tuple[str, str]]:
    """(entity_type, label) пары, встречающиеся у ДВУХ и более сущностей
    в этом окне — сигнал «голого совпадения label недостаточно» (§14.7,
    `same_label_different_entities`: «Иванов»-терапевт и «Иванов»-юрист,
    один local_id на каждого, один и тот же текст фамилии). Вне этого
    случая (подавляющее большинство: один label — одна сущность) строгая
    проверка не нужна и только срезала бы recall на сущностях,
    упомянутых в нескольких атомах."""
    seen: set[tuple[str, str]] = set()
    dupes: set[tuple[str, str]] = set()
    for e in entities:
        key = (e.entity_type, e.label.strip().lower())
        if key in seen:
            dupes.add(key)
        seen.add(key)
    return frozenset(dupes)


def _entity_grounded_in(atom_evidence: str, entity: ExtractedEntity,
                        ambiguous: frozenset[tuple[str, str]] = frozenset()) -> bool:
    """Endpoint grounding. По умолчанию — обычный поиск label/alias по
    стему (устойчиво к падежам). Если у ЭТОЙ сущности (entity_type,
    label) встречается больше одного раза в окне — голого совпадения
    недостаточно: требуем, чтобы СВОЙ `evidence_quote` сущности (если он
    несёт больше, чем сам label — реальная цитата, где именно эту
    сущность нашли) пересекался с evidence именно этого атома."""
    key = (entity.entity_type, entity.label.strip().lower())
    if key in ambiguous:
        has_own_context = bool(entity.evidence_quote) and entity.evidence_quote != entity.label
        if has_own_context:
            return entity.evidence_quote in atom_evidence or atom_evidence in entity.evidence_quote
    return is_mentioned(atom_evidence, entity.label, entity.aliases)


#: Классификатор-существительное перед именем сущности сигнализирует
#: «это место», а не «это сторона события» — «в МАГАЗИНЕ «Комус»» vs
#: «работает В ООО «Ромашка»»: один и тот же предлог «в», разный смысл,
#: разница — именно в наличии такого нарицательного перед именем.
#: Найдено при офлайн-сверке с GOLDEN_CASES (`purchase_warranty`,
#: `long_dense_window`), не предполагалось заранее.
_VENUE_NOUN_MARKER_RE = re.compile(
    r"магазин\w*|кафе\w*|офис\w*|ресторан\w*|клиник\w*|поликлиник\w*|переговорн\w*",
    re.IGNORECASE)


def _label_starts_with_venue_marker(label: str) -> bool:
    """Тот же classifier-noun может быть не отдельным словом ПЕРЕД
    именем сущности, а первым словом самого label («клиника
    «Здоровье+»», «магазин «Комус»» — extractor уже включил его в имя).
    Найдено офлайн-сверкой с v3 benchmark (GOLDEN_CASES называет те же
    сущности голым именем — «Ситилинк», «Пушкинъ» — без classifier-noun
    в label, поэтому там достаточно было `_has_venue_marker_before`)."""
    words = _label_words(label)
    return bool(words) and bool(_VENUE_NOUN_MARKER_RE.fullmatch(words[0].lower()))


def _has_marker_before(evidence: str, label: str, marker_re: re.Pattern[str], window_size: int = 3) -> bool:
    pos = _first_mention_index(evidence, label)
    if pos <= 0:
        return False
    tokens = _tokens(evidence)
    window = tokens[max(0, pos - window_size):pos]
    return any(marker_re.fullmatch(t) for t in window)


def _has_venue_marker_before(evidence: str, label: str) -> bool:
    return _label_starts_with_venue_marker(label) or _has_marker_before(evidence, label, _VENUE_NOUN_MARKER_RE)


#: «руководитель ОТДЕЛА логистики Титов» — должностной маркер перед
#: genitive-ORG называет чужую профессиональную принадлежность (Титова),
#: не саму организацию стороной события. Найдено офлайн-сверкой с v3
#: (`v3_project_meeting_2`): без исключения ORG проходила involves
#: grounding наравне с человеком, хотя текст называет её только местом
#: работы, не участником.
#: «представитель» намеренно НЕ входит: «представителя ЗАКАЗЧИКА —
#: компании X» вводит X именно как сторону события (апозиция, GOLDEN_CASES
#: `long_dense_window`), а не как чью-то организационную принадлежность —
#: в отличие от «руководитель ОТДЕЛА X», где X — не сторона, а контекст.
_AFFILIATION_TITLE_MARKER_RE = re.compile(
    r"руководител\w*|директор\w*|начальник\w*|глав\w*|сотрудник\w*|менеджер\w*|"
    r"специалист\w*|работник\w*",
    re.IGNORECASE)

#: «совещание ОТДЕЛА внедрения» — genitive-ORG сразу после существительного,
#: называющего сам факт/событие, — это чей это событие (владелец/контекст),
#: не участник наравне с людьми, перечисленными через «с участием». Найдено
#: офлайн-сверкой с v3 (`v3_project_meeting_full`).
_EVENT_CONTEXT_MARKER_RE = re.compile(r"совещани\w*|встреч\w*|собрани\w*|заседани\w*", re.IGNORECASE)


#: Владелец не мандатировал `owned_by`/`created_by` как auto-extractable
#: типы (они остаются в реестре, но `auto_extractable=false`) — но без
#: исключения PERSON/ORGANIZATION, упомянутые ТОЛЬКО как владелец или
#: автор чего-то, всё равно проходят involves grounding (label встречается
#: в evidence атома) и порождают involves, для которого typed precision
#: не видит разницы между «участник события» и «владелец объекта».
#: Найдено офлайн-сверкой с v3 benchmark: «Принадлежит компании X»,
#: «Отчёт составлен Ивановым», «Автор методики — Петров» — во всех этих
#: evidence PERSON/ORGANIZATION грамматически не участник, а
#: владелец/автор; маркер рядом с именем — сигнал придержать involves.
_OWNERSHIP_MARKER_RE = re.compile(
    r"принадлеж\w*|владел\w*|куплен\w*|составлен\w*|подготовлен\w*|созда\w*|"
    r"автор\w*|выпущен\w*|разработан\w*|написан\w*",
    re.IGNORECASE)
_OWNERSHIP_PROXIMITY_TOKENS = 3


def _has_ownership_marker_near(evidence: str, label: str) -> bool:
    span = _token_span(evidence, label)
    if span is None:
        return False
    tokens = _tokens(evidence)
    start, end = span
    window = tokens[max(0, start - _OWNERSHIP_PROXIMITY_TOKENS):end + 1 + _OWNERSHIP_PROXIMITY_TOKENS]
    return any(_OWNERSHIP_MARKER_RE.fullmatch(t) for t in window)


#: Узкий список — ТОЛЬКО истинные глаголы владения («принадлежит»,
#: «владеет»), НЕ весь `_OWNERSHIP_MARKER_RE` (который включает «куплен» —
#: а «X куплен В МЕСТЕ Y» это ровно обычный located_at паттерн, где Y
#: корректно место, не владение; «куплен» рядом с PLACE не должен её
#: исключать). Проверяется СРАЗУ после span сущности (0-2 токена), не в
#: широком окне — «Дача X ПРИНАДЛЕЖИТ Y»: X — грамматический субъект
#: владения, а не место события. Найдено офлайн-сверкой с v3
#: (`v3_family_property` для PLACE; та же логика симметрично защищает
#: ORGANIZATION-как-площадку от того же паттерна).
_OWNED_SUBJECT_MARKER_RE = re.compile(r"принадлеж\w*|владе\w*", re.IGNORECASE)


def _is_owned_subject(evidence: str, label: str) -> bool:
    span = _token_span(evidence, label)
    if span is None:
        return False
    tokens = _tokens(evidence)
    _, end = span
    window = tokens[end + 1:end + 3]
    return any(_OWNED_SUBJECT_MARKER_RE.fullmatch(t) for t in window)


def _compile_involves(atoms: Sequence[ExtractedAtom], entities: Sequence[ExtractedEntity],
                      ambiguous: frozenset[tuple[str, str]]) -> list[ExtractedEdge]:
    edges: list[ExtractedEdge] = []
    for atom in atoms:
        if atom.kind not in _INVOLVES_ATOM_KINDS:
            continue
        evidence = _atom_evidence(atom)
        tokens = _tokens(evidence)
        for entity in entities:
            if entity.entity_type not in _INVOLVES_ENTITY_TYPES:
                continue
            if not _entity_grounded_in(evidence, entity, ambiguous):
                continue
            if entity.entity_type == "ORGANIZATION" and _has_venue_marker_before(evidence, entity.label):
                # «в магазине X» — X здесь площадка, не сторона события;
                # это уже область located_at (см. там же), не involves.
                continue
            if entity.entity_type == "ORGANIZATION" and (
                    _has_marker_before(evidence, entity.label, _AFFILIATION_TITLE_MARKER_RE)
                    or _has_marker_before(evidence, entity.label, _EVENT_CONTEXT_MARKER_RE)):
                # «руководитель ОТДЕЛА X» (чужая принадлежность) и
                # «совещание ОТДЕЛА X» (чьё событие) — X здесь не сторона
                # события, а контекст/принадлежность другой сущности.
                continue
            if _has_ownership_marker_near(evidence, entity.label):
                # «принадлежит X», «составлен X» — X владелец/автор, не
                # участник события; owned_by/created_by не auto-extractable
                # (владелец), значит эта пара — NO EDGE, не involves.
                continue
            role = None
            if entity.entity_type == "PERSON":
                pos = _first_mention_index(evidence, entity.label)
                if pos > 0:
                    window_before = tokens[max(0, pos - _ROLE_PROXIMITY_TOKENS):pos]
                    if any(_DOCTOR_MARKER_RE.fullmatch(t) for t in window_before):
                        role = "doctor"
            edges.append(ExtractedEdge(
                from_local_id=atom.local_id, relation_type=SemanticRelationType.INVOLVES.value,
                to_local_id=entity.local_id, role=role, evidence_quote=evidence))
    return edges


# ---------------------------------------------------------------------------
# HAS_ROLE — ENTITY(PERSON) → ENTITY(CONCEPT). Владелец: «HAS_ROLE создавать
# только из явно присутствующей в evidence роли/специальности, не из
# догадки. Например «врач-уролог Иванов» допускает role/specialty
# extraction; просто «Иванов» — нет.» Реализация: оба label должны
# встречаться в ОДНОМ И ТОМ ЖЕ evidence атома ВПЛОТНУЮ — это и есть «явно
# присутствует» (аппозиция), в отличие от «упомянуты где-то в общем
# предложении» (см. `_HAS_ROLE_PROXIMITY_TOKENS`). ORGANIZATION исключена
# из источника: во всех golden/v3 fixtures роль текстом закрепляется
# только за человеком лично, никогда за организацией как таковой (найдено
# офлайн-сверкой с v3 `v3_clinic_visit_specialty_2` — «кабинет
# ЭНДОКРИНОЛОГА поликлиники №4» ложно давал ORG has_role по голой
# смежности, при этом ни одна fixture не объявляет ORG→has_role entailed).
# ---------------------------------------------------------------------------

_HAS_ROLE_SOURCE_TYPES = frozenset({"PERSON"})
#: Найдено офлайн-сверкой с GOLDEN_CASES (`lecture_concept`): порог в 4
#: токена ловил случайное соседство «Лектор Соколов привёл пример
#: гиперинфляции» (PERSON и CONCEPT в одном предложении, но не в
#: ролевой конструкции). 0 — строгая смежность (никаких токенов между
#: спанами): ровно аппозитивный паттерн («врача-нефролога ИМЯ», где дефис
#: не входит в токен и spans соприкасаются без зазора). Порог в 1 токен
#: (прежнее значение) также пропускал «ИМЯ увлекается ТЕМОЙ» — глагол
#: интереса/хобби между именем и концептом, не ролевую аппозицию (найдено
#: офлайн-сверкой с v3 `v3_family_property`) — 0 отсекает и это, не теряя
#: ни одного положительного случая ни в одной из fixture (все они —
#: спаны без зазора).
_HAS_ROLE_PROXIMITY_TOKENS = 0


def _find_has_role_matches(atoms: Sequence[ExtractedAtom],
                           entities: Sequence[ExtractedEntity]) -> list[tuple[str, str, str]]:
    """(atom.local_id, person_or_org.local_id, concept.local_id) для
    каждой ролевой аппозиции — общая логика для `_compile_has_role` и
    для исключения тех же (atom, concept) пар из `_compile_about` (см.
    там же: концепт, уже занятый ролью в ЭТОМ атоме, не может там же
    быть темой — иначе одна пара конкурирует за два типа)."""
    matches: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    persons_orgs = [e for e in entities if e.entity_type in _HAS_ROLE_SOURCE_TYPES]
    concepts = [e for e in entities if e.entity_type == "CONCEPT"]
    if not persons_orgs or not concepts:
        return matches
    for atom in atoms:
        evidence = _atom_evidence(atom)
        for person_or_org in persons_orgs:
            person_span = _token_span(evidence, person_or_org.label)
            if person_span is None:
                continue
            for concept in concepts:
                key = (person_or_org.local_id, concept.local_id)
                if key in seen:
                    continue
                concept_span = _token_span(evidence, concept.label)
                if concept_span is None:
                    continue
                gap = max(person_span[0] - concept_span[1], concept_span[0] - person_span[1]) - 1
                if gap > _HAS_ROLE_PROXIMITY_TOKENS:
                    continue
                seen.add(key)
                matches.append((atom.local_id, person_or_org.local_id, concept.local_id))
    return matches


def _compile_has_role(atoms: Sequence[ExtractedAtom],
                      has_role_matches: list[tuple[str, str, str]]) -> list[ExtractedEdge]:
    edges: list[ExtractedEdge] = []
    atom_evidence_by_id = {a.local_id: _atom_evidence(a) for a in atoms}
    for atom_id, person_or_org_id, concept_id in has_role_matches:
        edges.append(ExtractedEdge(
            from_local_id=person_or_org_id, relation_type=SemanticRelationType.HAS_ROLE.value,
            to_local_id=concept_id, evidence_quote=atom_evidence_by_id[atom_id]))
    return edges


# ---------------------------------------------------------------------------
# ABOUT — ATOM(event|fact|decision|concept) → ENTITY(CONCEPT). Только CONCEPT
# намеренно: ORGANIZATION/PLACE — законные цели involves/located_at, и
# допустить их сюда тоже означало бы, что одна и та же пара (atom, org)
# может породить involves И about одновременно без способа решить, какое
# из двух верно, — типовая typed precision штрафует ЛЮБОЕ лишнее ребро
# как false positive, поэтому конфликт разрешается заранее конструкцией,
# а не эвристикой различения «участник vs тема».
# ---------------------------------------------------------------------------

_ABOUT_ATOM_KINDS = frozenset({"event", "fact", "decision", "concept"})

#: «кабинет ЭНДОКРИНОЛОГА поликлиники» — концепт-специальность здесь
#: genitive-модификатор помещения (чей это кабинет), не тема самого
#: события/факта — has_role для него не подтверждён в ЭТОМ атоме (нужная
#: аппозиция «Лебедева — эндокринолог» — в другом предложении, не в этом
#: атоме, `has_role_concepts` его не ловит), но about тоже неверна.
#: Найдено офлайн-сверкой с v3 (`v3_clinic_visit_specialty_2`, тот же
#: genitive-паттерн, что и «совещание ОТДЕЛА»/«руководитель ОТДЕЛА» для
#: involves — только для ABOUT, не для involves/has_role).
_ABOUT_LOCATION_CONTEXT_MARKER_RE = re.compile(r"кабинет\w*|офис\w*|приёмн\w*", re.IGNORECASE)

#: P5 (владелец 2026-09-04, precision-first после R4 RCA `fact_plain`):
#: голого совпадения label концепта в evidence недостаточно — ABOUT
#: требует явного textual evidence того, что атом ДЕЙСТВИТЕЛЬНО вводит
#: тему/понятие/определение, а не того, что extractor решил назвать
#: какой-то кусок текста CONCEPT. `atom.kind == "concept"` уже сам по
#: себе такое доказательство (это и есть контракт SemanticNodeKind.CONCEPT
#: — «описание понятия», см. `NODE_SYSTEM_PROMPT` в `semantic_extract.py`)
#: — для него доп. маркер не нужен. Для остальных kind (event/fact/decision)
#: требуем явный топикальный/дефиниционный оборот: предлог «о/об/про»,
#: слово «тема/понятие/определение», либо «пример X» (см. GOLDEN_CASES
#: `lecture_concept`: «привёл пример гиперинфляции» — fact-атом, тема
#: вводится явно словом «пример», не голым упоминанием).
_ABOUT_TOPIC_MARKER_RE = re.compile(
    r"\bо\b|\bоб\b|\bпро\b|тем[а-я]*|понят[а-я]*|определени[а-я]*|пример[а-я]*",
    re.IGNORECASE)


def _has_about_topic_evidence(atom: ExtractedAtom, evidence: str) -> bool:
    if atom.kind == "concept":
        return True
    return bool(_ABOUT_TOPIC_MARKER_RE.search(evidence))


def _compile_about(atoms: Sequence[ExtractedAtom], entities: Sequence[ExtractedEntity],
                   ambiguous: frozenset[tuple[str, str]],
                   has_role_concepts: frozenset[str] = frozenset()) -> list[ExtractedEdge]:
    """`has_role_concepts` — local_id концептов, для которых где-либо в
    окне доказана ролевая аппозиция (`_find_has_role_matches`): концепт,
    однажды подтверждённый как чья-то роль/специальность («врача-нефролога
    Гавриловой» → «нефролог»), исключён из about ВО ВСЕХ атомах этого
    окна, не только там, где нашлась сама аппозиция — упоминание того же
    названия специальности в другом факте того же окна («приём был
    посвящён теме...») по-прежнему описывает роль, а не независимую тему
    (найдено офлайн-сверкой с v3 `v3_project_meeting_full`: та же пара
    «руководитель проекта» повторно грамматически именует роль во ВТОРОМ,
    более удалённом от аппозиции предложении, где расстояние уже не
    попадает в порог `_HAS_ROLE_PROXIMITY_TOKENS`, но семантика та же)."""
    edges: list[ExtractedEdge] = []
    concepts = [e for e in entities if e.entity_type == "CONCEPT"]
    if not concepts:
        return edges
    for atom in atoms:
        if atom.kind not in _ABOUT_ATOM_KINDS:
            continue
        evidence = _atom_evidence(atom)
        if not _has_about_topic_evidence(atom, evidence):
            continue
        for concept in concepts:
            if concept.local_id in has_role_concepts:
                continue
            if not _entity_grounded_in(evidence, concept, ambiguous):
                continue
            if _has_marker_before(evidence, concept.label, _ABOUT_LOCATION_CONTEXT_MARKER_RE, window_size=1):
                continue
            if len(_label_words(concept.label)) > 1 and _token_span(evidence, concept.label) is None:
                # Многословный label (e.g. «аномальные снегопады») должен
                # встречаться КАК ФРАЗА, не как рассеянные по предложению
                # отдельные слова — иначе bag-of-words grounding ложно
                # засчитывает тему, которую предложение на самом деле не
                # называет (найдено офлайн-сверкой с v3
                # `v3_weather_delay_chain`: «снегопад был аномально
                # сильным» содержит оба корня, но не саму фразу-тему).
                continue
            edges.append(ExtractedEdge(
                from_local_id=atom.local_id, relation_type=SemanticRelationType.ABOUT.value,
                to_local_id=concept.local_id, evidence_quote=evidence))
    return edges


# ---------------------------------------------------------------------------
# LOCATED_AT — ATOM(event|fact) → ENTITY(PLACE), плюс ORGANIZATION-как-
# площадка (магазин/кафе/офис) — НО только когда classifier-noun явно
# называет её местом («в МАГАЗИНЕ «Комус»»), иначе ORGANIZATION остаётся
# целью involves («работает В ООО «Ромашка»» — тот же предлог «в», но без
# classifier-noun вводит сторону события, не место). Найдено офлайн-
# сверкой с GOLDEN_CASES (`purchase_warranty`): без этого различения
# «в X» одинаково читалось бы и там, и там — конфликт решается наличием
# самого classifier-noun, не догадкой о смысле глагола.
# ---------------------------------------------------------------------------

_LOCATED_AT_ATOM_KINDS = frozenset({"event", "fact"})

#: P4 (владелец 2026-09-04, remediation после R4 RCA run 241, B5): «если
#: evidence/type combination противоречит контракту relation family — NO
#: EDGE, не догадка». Контракт LOCATED_AT — физическое место САМОГО
#: факта/события, не любое упоминание PLACE-сущности рядом с атомом; до
#: этой правки PLACE-ветка (в отличие от ORGANIZATION-venue-ветки чуть
#: ниже, у которой уже был `_has_venue_marker_before`) грундинг проверяла,
#: но локативный контекст — нет, и мнимый PLACE (extractor присвоил
#: entity_type=PLACE тому, что не место, но грундинг всё равно
#: срабатывает по голому совпадению label) получал LOCATED_AT без единого
#: locative-маркера. Симметрично уже принятому паттерну (venue-marker для
#: ORGANIZATION, ownership-marker для owned subject) — не generic type
#: coercion, а тот же класс evidence-контракта, что уже есть в этом файле.
_LOCATIVE_PREPOSITION_RE = re.compile(r"в|во|на|у", re.IGNORECASE)


def _has_locative_marker_before(evidence: str, label: str) -> bool:
    return _has_marker_before(evidence, label, _LOCATIVE_PREPOSITION_RE, window_size=1)


def _compile_located_at(atoms: Sequence[ExtractedAtom], entities: Sequence[ExtractedEntity],
                        ambiguous: frozenset[tuple[str, str]]) -> list[ExtractedEdge]:
    edges: list[ExtractedEdge] = []
    places = [e for e in entities if e.entity_type == "PLACE"]
    venues = [e for e in entities if e.entity_type == "ORGANIZATION"]
    if not places and not venues:
        return edges
    for atom in atoms:
        if atom.kind not in _LOCATED_AT_ATOM_KINDS:
            continue
        evidence = _atom_evidence(atom)
        for place in places:
            if not _entity_grounded_in(evidence, place, ambiguous):
                continue
            if _is_owned_subject(evidence, place.label):
                # «Дача X принадлежит Y» — X здесь предмет владения
                # (owned_by, не auto-extractable), не место, где произошёл
                # сам факт; найдено офлайн-сверкой с v3 `v3_family_property`.
                continue
            if not _has_locative_marker_before(evidence, place.label):
                # Fail-close (P4): грундинг сам по себе не доказывает
                # локативную роль — без «в/во/на/у» перед именем места
                # это не контракт LOCATED_AT, NO EDGE, а не догадка.
                continue
            edges.append(ExtractedEdge(
                from_local_id=atom.local_id, relation_type=SemanticRelationType.LOCATED_AT.value,
                to_local_id=place.local_id, evidence_quote=evidence))
        for org in venues:
            if not _entity_grounded_in(evidence, org, ambiguous):
                continue
            if not _has_venue_marker_before(evidence, org.label):
                continue
            if _is_owned_subject(evidence, org.label):
                continue
            edges.append(ExtractedEdge(
                from_local_id=atom.local_id, relation_type=SemanticRelationType.LOCATED_AT.value,
                to_local_id=org.local_id, evidence_quote=evidence))
    return edges


# ---------------------------------------------------------------------------
# Causal/support — REASON_FOR / RESULTED_IN / SUPPORTS. Владелец: «создавать
# только при явных lexical/structural cues... Без явного causal/support
# evidence → NO EDGE.» Список cues — дословно из мандата, НЕ расширен
# синонимами («на основании», «стало причиной» и т.п. сознательно не
# добавлены — иначе граница «явный маркер» против «правдоподобная
# интерпретация» размывается тем же способом, которым владелец запретил
# добиваться recall). Cue ищется в EVIDENCE ЦЕЛЕВОГО атома (тот, что
# grammatически ссылается назад/вперёд на пару) — не «где-то между
# атомами в окне», это невозможно детерминированно без character offsets,
# которых `ExtractedAtom` не несёт. Пара — соседние атомы в порядке
# извлечения (см. модульный docstring выше про recall-tradeoff).
# ---------------------------------------------------------------------------

_REASON_FOR_TARGET_KINDS = frozenset({"decision"})
_REASON_FOR_SOURCE_KINDS = frozenset({"fact", "event"})
#: Неизменяемые предлог/наречие — не спрягаются, буквальная подстрока
#: достаточна.
_REASON_FOR_CUE_RE = re.compile(r"из-за|вследствие|поэтому", re.IGNORECASE)

_RESULTED_IN_TARGET_KINDS = frozenset({"event", "fact"})
_RESULTED_IN_SOURCE_KINDS = frozenset({"event", "fact", "decision"})
#: «привёл/привело/привела/привели ... к» — требуем именно эту связку
#: (не голый стем «привел», который также значит «привести пример», не
#: причинность); «в результате» — устойчивая словоформа, не спрягается.
_RESULTED_IN_CUE_RE = re.compile(r"прив[её]л\w*\s+к\b|в результате", re.IGNORECASE)

_SUPPORTS_TARGET_KINDS = frozenset({"fact", "decision"})
_SUPPORTS_SOURCE_KINDS = frozenset({"fact"})
#: Стем глагола — покрывает лицо/число (подтверждает/подтверждают/
#: подтвердил, обосновывает/обосновывают).
_SUPPORTS_CUE_RE = re.compile(r"подтвержда\w*|обоснов\w*", re.IGNORECASE)


def _has_cue(text: str, pattern: re.Pattern[str]) -> bool:
    return pattern.search(text) is not None


def _content_word_stems(text: str) -> set[str]:
    """Слова длиннее 3 символов (не предлоги/союзы) по стему — единицы
    сравнения для `_best_overlap_target`, не для endpoint grounding."""
    return {_stem(t) for t in _tokens(text) if len(t) > 3}


#: Сентинел: «пересечения не нашлось вообще» (анафорический случай,
#: адрес разрешает `_nearest_kind_eligible`) — отличается от `None`
#: («пересечение есть, но неоднозначно» — тогда соседство НЕ резерв).
_OVERLAP_NONE_FOUND = object()


def _best_overlap_target(cue_evidence: str, candidates: Sequence[ExtractedAtom]) -> ExtractedAtom | None:
    """RESULTED_IN/SUPPORTS: источник cue часто сам НАЗЫВАЕТ предмет цели
    («Чек ... подтверждает ФАКТ ПОКУПКИ ПРИНТЕРА», «... привело к
    ПЕРЕСМОТРУ ПЛАНА ПРОИЗВОДСТВА») — целевой атом ищем по пересечению
    content-слов с ЛЮБЫМ kind-eligible атомом окна, не только с соседним
    по списку (найдено офлайн-сверкой с v3 `v3_purchase_ownership`: цель
    cue-атома там не примыкает к нему в списке атомов вообще). Пересечение
    сравнивается по `startswith` в обе стороны (не по равенству стемов) —
    `_stem()` усечение зависит от длины исходного слова, поэтому
    «принтер»/«принтера» дают разные по длине, но один из них — префикс
    другого.

    Возвращает `None`, если пересечения нет вообще (анафорический случай
    — см. `_nearest_kind_eligible`), И если оно есть, но неоднозначно
    (несколько атомов с одинаковым максимумом) — в этом ВТОРОМ случае
    вызывающий код НЕ должен откатываться на соседство: раз cue-атом
    явно НАЗВАЛ предмет цели, а несколько атомов одинаково подходят под
    это название, угадывание через позицию — это ровно inference, от
    которого владелец предостерёг (найдено офлайн-сверкой с v3
    `v3_decision_supersede_2`: неоднозначное совпадение по «лимит
    расходов» между тремя атомами; ближайший сосед оказывается НЕ тем,
    что называет cue, — соседство здесь не резерв, а неверная догадка)."""
    cue_stems = _content_word_stems(cue_evidence)
    if not cue_stems:
        return None
    scored: list[tuple[int, ExtractedAtom]] = []
    for candidate in candidates:
        candidate_stems = _content_word_stems(_atom_evidence(candidate))
        overlap = sum(
            1 for cs in cue_stems
            if any(cs.startswith(ds) or ds.startswith(cs) for ds in candidate_stems))
        if overlap > 0:
            scored.append((overlap, candidate))
    if not scored:
        return _OVERLAP_NONE_FOUND
    scored.sort(key=lambda sc: sc[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _nearest_kind_eligible(ordered: list[ExtractedAtom], index: int,
                           target_kinds: frozenset[str]) -> ExtractedAtom | None:
    """Резерв, когда `_best_overlap_target` не нашёл ВООБЩЕ никакого
    пересечения content-слов — типовой анафорический паттерн («...
    подтверждают ЭТОТ ВЫВОД», без называния предмета) — тогда сосед по
    списку остаётся приемлемой заменой, но только если он ЕДИНСТВЕННЫЙ
    подходящего kind (сосед и до, и после — неоднозначность, NO EDGE, не
    угадывание направления)."""
    candidates = []
    if index + 1 < len(ordered) and ordered[index + 1].kind in target_kinds:
        candidates.append(ordered[index + 1])
    if index - 1 >= 0 and ordered[index - 1].kind in target_kinds:
        candidates.append(ordered[index - 1])
    return candidates[0] if len(candidates) == 1 else None


def _compile_reason_for(ordered: list[ExtractedAtom]) -> list[ExtractedEdge]:
    """REASON_FOR — cue встречается в evidence ЦЕЛЕВОГО decision-атома (он
    грамматически ссылается назад на причину: «...Из-за этого было
    решено...» — анафора, не называние предмета причины по content-словам)
    — соседняя пара остаётся единственно устойчивым способом её найти."""
    edges: list[ExtractedEdge] = []
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if (a.kind in _REASON_FOR_SOURCE_KINDS and b.kind in _REASON_FOR_TARGET_KINDS
                and _has_cue(_atom_evidence(b), _REASON_FOR_CUE_RE)):
            edges.append(ExtractedEdge(
                from_local_id=a.local_id, relation_type=SemanticRelationType.REASON_FOR.value,
                to_local_id=b.local_id, evidence_quote=_atom_evidence(b)))
    return edges


def _compile_source_named_causal(ordered: list[ExtractedAtom], source_kinds: frozenset[str],
                                 target_kinds: frozenset[str], cue_re: re.Pattern[str],
                                 relation_type: str) -> list[ExtractedEdge]:
    """RESULTED_IN/SUPPORTS: cue в evidence ИСТОЧНИКА (он называет своё
    следствие/то, что подтверждает). Цель — `_best_overlap_target`,
    резерв — `_nearest_kind_eligible` (см. обе docstring)."""
    edges: list[ExtractedEdge] = []
    for i, a in enumerate(ordered):
        if a.kind not in source_kinds:
            continue
        evidence = _atom_evidence(a)
        if not _has_cue(evidence, cue_re):
            continue
        candidates = [b for b in ordered if b is not a and b.kind in target_kinds]
        overlap_target = _best_overlap_target(evidence, candidates)
        if overlap_target is _OVERLAP_NONE_FOUND:
            target = _nearest_kind_eligible(ordered, i, target_kinds)
        else:
            target = overlap_target
        if target is None:
            continue
        edges.append(ExtractedEdge(
            from_local_id=a.local_id, relation_type=relation_type,
            to_local_id=target.local_id, evidence_quote=evidence))
    return edges


def _compile_causal(atoms: Sequence[ExtractedAtom]) -> list[ExtractedEdge]:
    ordered = list(atoms)
    edges: list[ExtractedEdge] = []
    edges.extend(_compile_reason_for(ordered))
    edges.extend(_compile_source_named_causal(
        ordered, _RESULTED_IN_SOURCE_KINDS, _RESULTED_IN_TARGET_KINDS,
        _RESULTED_IN_CUE_RE, SemanticRelationType.RESULTED_IN.value))
    edges.extend(_compile_source_named_causal(
        ordered, _SUPPORTS_SOURCE_KINDS, _SUPPORTS_TARGET_KINDS,
        _SUPPORTS_CUE_RE, SemanticRelationType.SUPPORTS.value))
    return edges


def compile_relations(entities: Sequence[ExtractedEntity], atoms: Sequence[ExtractedAtom],
                      window_text: str = "") -> list[ExtractedEdge]:
    """Единственный источник рёбер production-пути (R4.7). `window_text`
    принят для контракта совместимости с местом вызова (§14.4.2
    grounding уже случился в `semantic_extract.validate()`) — правила
    ниже сами не проверяют «это подстрока окна», они проверяют «endpoint
    упомянут в evidence СВОЕГО атома/сущности», что строже.

    Возвращает НОВЫЙ список — не трогает вход. Никогда не возвращает
    `related_to` и никогда — `derived_from` (см. `derive_from_source`)."""
    ambiguous = _duplicate_label_keys(entities)
    has_role_matches = _find_has_role_matches(atoms, entities)
    has_role_concepts = frozenset(concept_id for _, _, concept_id in has_role_matches)
    edges: list[ExtractedEdge] = []
    edges.extend(_compile_involves(atoms, entities, ambiguous))
    edges.extend(_compile_has_role(atoms, has_role_matches))
    edges.extend(_compile_about(atoms, entities, ambiguous, has_role_concepts))
    edges.extend(_compile_located_at(atoms, entities, ambiguous))
    edges.extend(_compile_causal(atoms))
    return edges


def derive_from_source(node_local_id: str, document_ref_local_id: str) -> ExtractedEdge:
    """DERIVED_FROM (владелец): «Вообще не отдавать модели: semantic node
    → source/document. Строить полностью детерминированно из provenance
    ingest pipeline.» Не текстовое правило — чистая структурная функция:
    вызывающий (публикация в граф, `semantic_publish.py`) передаёт
    local_id узла и local_id уже материализованного `DOCUMENT_REF` узла
    источника; про текст окна эта функция не знает и не читает вообще.

    ПРОБЕЛ (найдено при проектировании R4.7, не живым прогоном): на
    сегодня НИ ОДИН код не создаёт `DOCUMENT_REF`-узлы из
    `knowledge_sources` (модели/base.py описывает их как «заводимые
    детерминированно», но материализации нет) — эта функция готова к
    использованию, но её некому вызвать до того, как появится сама
    материализация. Ни `RELATION_BENCHMARK_V3_CASES` (нет понятия
    документа-узла), ни `GOLDEN_CASES` (то же) не могут проверить эту
    функцию — оба бенчмарка ниже честно показывают 0 покрытия
    `derived_from`, это ожидаемо, не дефект compiler'а."""
    return ExtractedEdge(
        from_local_id=node_local_id, relation_type=SemanticRelationType.DERIVED_FROM.value,
        to_local_id=document_ref_local_id, evidence_quote="")
