"""R4.6.F1.2 (владелец 03.09.2026) — `RelationVerbalizerV2` устранял
подстановку `canonical_text` как именной группы, но заменил её на
РОДОВУЮ ссылку («описанное событие»), которая неоднозначна, если в
кейсе больше одного атома того же `kind` — обнаружено живым прогоном
R4.6.F1.1 audit: `long_dense_window` (7 атомов, два `event`) и
`typed_relations_variety` (4 атома, все `fact`) полностью выпали из v2
именно по этой причине, а не потому что для них не было verbalizer'а.

Этот модуль заменяет родовую ссылку на ДЕТЕРМИНИРОВАННУЮ quoted
reference: kind-noun (склоняемый по нужному падежу, та же таблица форм,
что в v2) + дословная цитата `canonical_text` в кавычках, например
`событие «20 января 2026 года состоялось совещание...»`,
`факт «тестирование проекта не завершено»`, `решение «перенести запуск
проекта на октябрь»`. Цитата уникальна для каждого атома внутри кейса
по построению (два разных атома не имеют идентичного `canonical_text`)
— guard на неоднозначность («ambiguous atom-kind reference»,
центральный механизм v2) здесь БОЛЬШЕ НЕ НУЖЕН и не воспроизводится:
задача, которую он решал, устранена на уровне verbalizer'а, а не
дополнительной проверкой вызывающей стороны.

Реестр `(relation_type, source_category:kind, target_category:kind)`
расширен относительно v2 до полного контракта онтологии всех 15 типов
(`docs/R4.6.F1.2-RELATION-ONTOLOGY.md`) — включая 8 типов, отсутствующих
в существующих golden fixtures (`has_role`, `part_of`, `created_by`,
`owned_by`, `contradicts`, `supersedes`, `derived_from`, `refers_to`):
для них нет исторических примеров для сверки, поэтому verbalizer здесь
и есть единственный источник контракта — сверяется вручную с
ontology-документом, не с живыми данными.

Domain-agnostic, никакого LLM, никакой эвристики словоизменения имён:
где нужна была бы декленация ПРОИЗВОЛЬНОГО `ENTITY.label` (падеж,
род/число глагола), функция вместо этого либо ставит сущность в позицию
именительного падежа (подлежащее или предикатив после тире — «Автор
{X} — {label}»), либо оборачивает `label` в кавычки после уже
просклонённого нарицательного существительного («роль «{label}»»,
«теме {label}») — та же техника, что в v2 не даёт вложенных кавычек и
не требует знать род/склонение имени."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: Владелец: `related_to` явно симметричен; `contradicts` — логически
#: симметричен (A противоречит B ⇔ B противоречит A). Полный контракт —
#: `docs/R4.6.F1.2-RELATION-ONTOLOGY.md` таблица направленности.
SYMMETRIC_RELATION_TYPES: frozenset[str] = frozenset({"related_to", "contradicts"})

UNSUPPORTED_FOR_NLI = "UNSUPPORTED_FOR_NLI"

NodeCategory = Literal["ENTITY", "ATOM"]

CaseForm = Literal["nom", "prep", "gen", "dat", "ins"]

#: Полная падежная парадигма на каждый ATOM.kind, плюс формы согласования
#: глагола/причастия по роду для тех verbalizer'ов, где атом — подлежащее
#: (`located_at`: "произошло/произошёл"; `resulted_in`: "привело/привёл";
#: `derived_from`: "основано/основан"). Собрано вручную, не эвристикой —
#: новый падёж/kind добавляется явной записью.
_KIND_FORMS: dict[str, dict[CaseForm, str]] = {
    "event": {"nom": "событие", "prep": "событии", "gen": "события",
             "dat": "событию", "ins": "событием"},
    "fact": {"nom": "факт", "prep": "факте", "gen": "факта",
            "dat": "факту", "ins": "фактом"},
    "decision": {"nom": "решение", "prep": "решении", "gen": "решения",
                "dat": "решению", "ins": "решением"},
    "concept": {"nom": "понятие", "prep": "понятии", "gen": "понятия",
               "dat": "понятию", "ins": "понятием"},
}

_PAST_AGREE = {"event": "произошло", "fact": "произошёл", "decision": "произошло"}
_LED_TO_AGREE = {"event": "привело", "fact": "привёл", "decision": "привело"}
_BASED_ON_AGREE = {"event": "основано", "fact": "основан", "decision": "основано"}

#: Дательный падеж существительного-темы для `about` (владелец: топик —
#: не только CONCEPT/ORGANIZATION; PLACE как топик — «относится к
#: месту X», дательный от «место» — «месту», НЕ «месте» (это
#: предложный) — поймано на этапе написания, не живым прогоном.
_ABOUT_TOPIC_NOUN = {"CONCEPT": "теме", "ORGANIZATION": "организации", "PLACE": "месту"}


@dataclass(frozen=True)
class Node:
    """`category="ENTITY"`: `ref_kind` — entity_type (PERSON/ORGANIZATION/
    PLACE/CONCEPT), `label` — реальная именная группа (используется как
    есть, без декленации). `category="ATOM"`: `ref_kind` — kind
    (event/fact/decision/concept), `label` — `canonical_text` атома
    (В ОТЛИЧИЕ ОТ v2 — здесь используется, это и есть quoted reference,
    не родовая замена)."""

    category: NodeCategory
    ref_kind: str
    label: str = ""


def _entity_label(node: Node) -> str:
    return node.label


def _quoted(node: Node, case: CaseForm) -> str:
    """`<падежная форма kind-noun> «<canonical_text>»` — цитата не
    склоняется (как и любая кавычная цитата/название в русском:
    ср. «в фильме «Летят журавли»» — «фильм» склоняется, заголовок
    внутри кавычек — нет)."""
    return f"{_KIND_FORMS[node.ref_kind][case]} «{node.label}»"


def _quoted_cap(node: Node, case: CaseForm) -> str:
    text = _quoted(node, case)
    return text[0].upper() + text[1:]


def _verbalize_involves(a: Node, b: Node) -> str:
    if b.ref_kind not in ("PERSON", "ORGANIZATION", "PLACE"):
        return UNSUPPORTED_FOR_NLI
    # Направление ПЕРЕСТАВЛЕНО относительно naive from/to (§14.9 gloss —
    # «атом вовлекает участника»): участник (b) — грамматический субъект.
    return f"{_entity_label(b)} участвует в {_quoted(a, 'prep')}."


def _verbalize_has_role(a: Node, b: Node) -> str:
    if a.ref_kind not in ("PERSON", "ORGANIZATION") or b.ref_kind != "CONCEPT":
        return UNSUPPORTED_FOR_NLI
    # `label_a` — подлежащее (именительный, декленация не нужна);
    # `label_b` в кавычках как обозначение роли — тоже без декленации.
    return f"{_entity_label(a)} занимает роль «{_entity_label(b)}»."


def _verbalize_about(a: Node, b: Node) -> str:
    # Найдено при написании v3 fixtures: событие тоже может иметь тему
    # («лекция была посвящена теме X») — исключение EVENT было излишне
    # узким (ontology contract исправлен вслед за этим).
    if a.ref_kind not in ("event", "concept", "fact", "decision"):
        return UNSUPPORTED_FOR_NLI
    noun = _ABOUT_TOPIC_NOUN.get(b.ref_kind)
    if noun is None:
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} относится к {noun} {_entity_label(b)}."


def _verbalize_located_at(a: Node, b: Node) -> str:
    if a.ref_kind not in ("event", "fact"):
        return UNSUPPORTED_FOR_NLI
    if b.ref_kind not in ("PLACE", "ORGANIZATION"):
        return UNSUPPORTED_FOR_NLI
    # PLACE-лейбл без собственных кавычек (топоним вроде «Казань») нельзя
    # склонить в предложный падеж без словаря — найдено живым прогоном
    # recovery-check («в Казань» вместо «в Казани»). Классификатор «месте»
    # (уже в предложном падеже) перед лейблом снимает необходимость
    # склонения самого имени — тот же приём, что в `_verbalize_about`.
    # ORGANIZATION-лейблы в fixtures уже несут собственные кавычки/
    # классификатор («кафе «Пушкинъ»», «магазин «Ситилинк»») — классификатор
    # не добавляется, иначе получится двойной («в организации «Ситилинк»»
    # при уже квалифицированном лейбле — не ошибка, но избыточно).
    # Лейбл уже несёт собственный классификатор/кавычки («кафе «Пушкинъ»»)
    # — добавлять «месте» было бы избыточно (не ошибка, но лишнее).
    prefix = "месте " if b.ref_kind == "PLACE" and "«" not in b.label else ""
    return f"{_quoted_cap(a, 'nom')} {_PAST_AGREE[a.ref_kind]} в {prefix}{_entity_label(b)}."


def _verbalize_part_of(a: Node, b: Node) -> str:
    # Ограничено ORGANIZATION-ORGANIZATION (подразделение — часть
    # организации): PLACE-PLACE потребовал бы склонения произвольного
    # топонима в родительном падеже («в состав <?>») — недоступно без
    # словаря/эвристики имени, см. модуль docstring.
    if a.ref_kind != "ORGANIZATION" or b.ref_kind != "ORGANIZATION":
        return UNSUPPORTED_FOR_NLI
    return f"{_entity_label(a)} входит в состав организации {_entity_label(b)}."


def _verbalize_created_by(a: Node, b: Node) -> str:
    if a.ref_kind not in ("fact", "decision") or b.ref_kind not in ("PERSON", "ORGANIZATION"):
        return UNSUPPORTED_FOR_NLI
    # Предикатив через тире вместо «создано {b}» (творительный падёж
    # произвольного имени недоступен без декленации) — `label_b` в
    # именительном, декленация не нужна.
    return f"Автор {_quoted(a, 'gen')} — {_entity_label(b)}."


def _verbalize_owned_by(a: Node, b: Node) -> str:
    if a.ref_kind != "fact" or b.ref_kind not in ("PERSON", "ORGANIZATION"):
        return UNSUPPORTED_FOR_NLI
    return f"Владелец {_quoted(a, 'gen')} — {_entity_label(b)}."


def _verbalize_resulted_in(a: Node, b: Node) -> str:
    if a.ref_kind not in ("event", "fact", "decision") or b.ref_kind not in ("event", "fact"):
        return UNSUPPORTED_FOR_NLI
    # v2 использовал «произошёл к» (бессмысленно для causation) —
    # исправлено на семантически верное «привело/привёл к» с
    # согласованием по роду источника (найдено при пересборке под
    # R4.6.F1.2, не живым прогоном).
    return f"{_quoted_cap(a, 'nom')} {_LED_TO_AGREE[a.ref_kind]} к {_quoted(b, 'dat')}."


def _verbalize_reason_for(a: Node, b: Node) -> str:
    if a.ref_kind not in ("fact", "event") or b.ref_kind != "decision":
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} — причина {_quoted(b, 'gen')}."


def _verbalize_supports(a: Node, b: Node) -> str:
    if a.ref_kind != "fact" or b.ref_kind not in ("fact", "decision"):
        return UNSUPPORTED_FOR_NLI
    # Винительный = именительный для неодушевлённых kind-noun — отдельная
    # форма не нужна (как в v2).
    return f"{_quoted_cap(a, 'nom')} подтверждает {_quoted(b, 'nom')}."


def _verbalize_contradicts(a: Node, b: Node) -> str:
    if a.ref_kind not in ("fact", "decision") or b.ref_kind not in ("fact", "decision"):
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} противоречит {_quoted(b, 'nom')}."


def _verbalize_supersedes(a: Node, b: Node) -> str:
    if a.ref_kind not in ("decision", "fact") or b.ref_kind not in ("decision", "fact"):
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} заменяет собой {_quoted(b, 'nom')}."


def _verbalize_derived_from(a: Node, b: Node) -> str:
    if a.ref_kind not in ("fact", "decision") or b.ref_kind not in ("fact", "decision"):
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} {_BASED_ON_AGREE[a.ref_kind]} на {_quoted(b, 'prep')}."


def _verbalize_refers_to(a: Node, b: Node) -> str:
    if a.ref_kind not in ("fact", "decision") or b.ref_kind not in ("event", "fact", "decision"):
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} ссылается на {_quoted(b, 'nom')}."


def _verbalize_related_to_entity(a: Node, b: Node) -> str:
    # Симметричная конструкция — переставленные аргументы дают ДРУГУЮ
    # строку, то же истинностное значение (как в v2).
    return f"Существует связь между «{_entity_label(a)}» и «{_entity_label(b)}»."


def _verbalize_related_to_atom(a: Node, b: Node) -> str:
    return f"Существует связь между {_quoted(a, 'ins')} и {_quoted(b, 'ins')}."


_VERBALIZERS = {
    ("involves", "ATOM", "ENTITY"): _verbalize_involves,
    ("has_role", "ENTITY", "ENTITY"): _verbalize_has_role,
    ("about", "ATOM", "ENTITY"): _verbalize_about,
    ("located_at", "ATOM", "ENTITY"): _verbalize_located_at,
    ("part_of", "ENTITY", "ENTITY"): _verbalize_part_of,
    ("created_by", "ATOM", "ENTITY"): _verbalize_created_by,
    ("owned_by", "ATOM", "ENTITY"): _verbalize_owned_by,
    ("resulted_in", "ATOM", "ATOM"): _verbalize_resulted_in,
    ("reason_for", "ATOM", "ATOM"): _verbalize_reason_for,
    ("supports", "ATOM", "ATOM"): _verbalize_supports,
    ("contradicts", "ATOM", "ATOM"): _verbalize_contradicts,
    ("supersedes", "ATOM", "ATOM"): _verbalize_supersedes,
    ("derived_from", "ATOM", "ATOM"): _verbalize_derived_from,
    ("refers_to", "ATOM", "ATOM"): _verbalize_refers_to,
    ("related_to", "ENTITY", "ENTITY"): _verbalize_related_to_entity,
    ("related_to", "ATOM", "ATOM"): _verbalize_related_to_atom,
}


def verbalize(relation_type: str, source: Node, target: Node) -> str:
    """Natural-language hypothesis или `UNSUPPORTED_FOR_NLI`, если для
    этой (relation_type, source.category, target.category) — или для
    конкретных `ref_kind` внутри неё — нет проверенного контракта
    (`docs/R4.6.F1.2-RELATION-ONTOLOGY.md`). Не форсирует строку ради
    покрытия enum."""
    fn = _VERBALIZERS.get((relation_type, source.category, target.category))
    if fn is None:
        return UNSUPPORTED_FOR_NLI
    return fn(source, target)
