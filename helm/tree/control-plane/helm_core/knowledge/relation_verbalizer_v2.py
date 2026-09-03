"""R4.6.F1.1 (владелец 03.09.2026) — исправление фундаментального
дефекта F1 dataset v1: `nli_relation_dataset.py` строил hypothesis,
подставляя `GoldAtom.canonical_text` (ЦЕЛОЕ ПРЕДЛОЖЕНИЕ) в шаблон вида
`"{a} участвует в {b}."`, как будто это именная группа — результат
грамматически и, для части типов, СЕМАНТИЧЕСКИ обратный (направление
`involves` — «атом вовлекает участника», а не «участник участвует в
атоме», см. R4.6.F1.1 audit).

Этот модуль разделяет NODE LABEL (что подставляется) и RELATION
VERBALIZATION (как строится предложение) — контракт, который знает:
  - тип узла-источника и узла-цели (`ENTITY`/`ATOM` + entity_type/kind),
  - relation_type,
  - направление.

`ENTITY` подставляется своим `label` — это уже короткая именная группа.
`ATOM` НЕ подставляется как `canonical_text` — вместо этого используется
родовая именная группа по `kind` («описанное событие/факт/решение/
понятие», склоняемая по нужному падежу вручную, БЕЗ LLM, см.
`_ATOM_KIND_FORMS`) — то же решение, что в примере владельца
(«Кириченко Сергей Александрович участвует в описанном событии.»).

Domain-agnostic: правила зависят от (relation_type, source_kind,
target_kind), а не от домена (health/work/purchases) — тот же принцип,
что у `relation_candidates.py` (R4.6.C2).

Контракт НЕ требует одного шаблона на relation_type (владелец п.4):
`relation_type × source_kind × target_kind` может иметь разные
verbalizations, и для большинства комбинаций закрытого реестра §14.9
(has_role/part_of/created_by/owned_by/contradicts/supersedes/
derived_from/refers_to — ни разу не встречаются в golden fixtures ни в
одной допустимой node-kind комбинации) естественного verbalizer нет:
такие комбинации возвращают `UNSUPPORTED_FOR_NLI`, а не притянутую
строку ради 100% покрытия enum.

Симметричные relation_type (владелец п.2): `related_to` — явно
симметричный per владелец. `contradicts` — тоже логически симметричен
(«A противоречит B» эквивалентно «B противоречит A»), в golden fixtures
не встречается ни разу (0 инстансов), но контракт зафиксирован здесь
на случай будущих fixtures. Для симметричных типов `reversed_direction`
НЕ является валидным hard-negative — переставленная пара тоже истинна."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: Владелец п.2: reversed_direction не может автоматически считаться
#: negative для симметричных relation_type. `related_to` — явно
#: симметричен (владелец). `contradicts` — логически симметричен (если
#: A противоречит B, то и B противоречит A) — 0 инстансов в golden
#: fixtures на сегодня, зафиксировано заранее, не задним числом.
SYMMETRIC_RELATION_TYPES: frozenset[str] = frozenset({"related_to", "contradicts"})

UNSUPPORTED_FOR_NLI = "UNSUPPORTED_FOR_NLI"

NodeCategory = Literal["ENTITY", "ATOM"]

#: Родовая именная группа на каждый ATOM.kind, в нужных падежах —
#: собрано вручную (не LLM), только те формы, которые реально нужны
#: зарегистрированным verbalizer'ам ниже. Новый падёж/kind — новая
#: явная запись, не эвристика словоизменения.
_ATOM_KIND_FORMS: dict[str, dict[str, str]] = {
    "event": {"nom": "Описанное событие", "prep": "описанном событии",
             "gen": "описанного события", "dat": "описанному событию",
             "past_agree": "произошло"},
    "fact": {"nom": "Описанный факт", "prep": "описанном факте",
            "gen": "описанного факта", "dat": "описанному факту",
            "past_agree": "произошёл"},
    "decision": {"nom": "Описанное решение", "prep": "описанном решении",
                "gen": "описанного решения", "dat": "описанному решению",
                "past_agree": "произошло"},
    "concept": {"nom": "Описанное понятие", "prep": "описанном понятии",
               "gen": "описанного понятия", "dat": "описанному понятию",
               "past_agree": "произошло"},
}


@dataclass(frozen=True)
class Node:
    """Узел hypothesis-предложения. `category="ENTITY"` — `ref_kind` это
    `entity_type` (PERSON/ORGANIZATION/PLACE/CONCEPT), `label` — реальная
    короткая именная группа. `category="ATOM"` — `ref_kind` это `kind`
    (event/fact/decision/concept), `label` игнорируется verbalizer'ом
    (используется только `atom_id` для проверки неоднозначности —
    см. `atom_kind_is_ambiguous_in_case`)."""

    category: NodeCategory
    ref_kind: str
    label: str = ""


def _entity_label(node: Node) -> str:
    return node.label


def _verbalize_involves(a: Node, b: Node) -> str:
    # Владелец: «EVENT/FACT -> PERSON, INVOLVES: "<PERSON> участвует в
    # описанном событии."» — направление ПЕРЕСТАВЛЕНО относительно
    # naive from/to: истинная семантика involves — «атом вовлекает
    # участника» (§14.9 gloss), то есть участник (b) грамматически
    # субъект «участвует», атом (a) — обстоятельство места.
    return f"{_entity_label(b)} участвует в {_ATOM_KIND_FORMS[a.ref_kind]['prep']}."


def _verbalize_located_at(a: Node, b: Node) -> str:
    # Вручную проверено (R4.6.F1.1 audit) только для PLACE/ORGANIZATION
    # целей («произошло в кафе»/«в «Ситилинк»» — естественно). CONCEPT
    # никогда не бывает «местом» — без этой проверки категория ENTITY
    # пропускала бы located_at к CONCEPT (нашлось живым прогоном audit:
    # "Описанное понятие произошло в уролог." — бессмысленно).
    if b.ref_kind not in ("PLACE", "ORGANIZATION"):
        return UNSUPPORTED_FOR_NLI
    forms = _ATOM_KIND_FORMS[a.ref_kind]
    return f"{forms['nom']} {forms['past_agree']} в {_entity_label(b)}."


def _verbalize_about_entity(a: Node, b: Node) -> str:
    # Без добавления собственных кавычек вокруг label: организации уже
    # обычно несут свои («ООО «Х»»), понятия — обычно нет («уролог») —
    # добавлять ещё одну пару поверх дало бы вложенные кавычки.
    noun = "теме" if b.ref_kind == "CONCEPT" else "организации"
    return f"{_ATOM_KIND_FORMS[a.ref_kind]['nom']} относится к {noun} {_entity_label(b)}."


def _verbalize_reason_for(a: Node, b: Node) -> str:
    return f"{_ATOM_KIND_FORMS[a.ref_kind]['nom']} — причина {_ATOM_KIND_FORMS[b.ref_kind]['gen']}."


def _verbalize_resulted_in(a: Node, b: Node) -> str:
    forms_a = _ATOM_KIND_FORMS[a.ref_kind]
    return f"{forms_a['nom']} {forms_a['past_agree']} к {_ATOM_KIND_FORMS[b.ref_kind]['dat']}."


def _verbalize_supports(a: Node, b: Node) -> str:
    # Инвариант рода/числа: «подтверждает» — настоящее время, 3-е лицо,
    # не согласуется с родом подлежащего — гендерных форм не нужно.
    # Винительный падёж у обоих родовых существительных (неодушевлённые
    # средний/мужской род) совпадает с именительным — отдельная форма
    # не нужна.
    return f"{_ATOM_KIND_FORMS[a.ref_kind]['nom']} подтверждает {_ATOM_KIND_FORMS[b.ref_kind]['nom'].lower()}."


def _verbalize_related_to_symmetric(a: Node, b: Node) -> str:
    # Конструкция БЕЗ согласования предиката по роду ни с одним из двух
    # произвольных ярлыков (род сущности по имени детерминированно не
    # определить без LLM/словаря) — и структурно симметрична, что и
    # отражает реальный контракт `related_to`.
    return f"Существует связь между «{_entity_label(a)}» и «{_entity_label(b)}»."


#: Реестр (relation_type, source_category:kind, target_category:kind) ->
#: verbalizer. Ключ — ИМЕННО те тройки, для которых естественная русская
#: verbalization проверена вручную (R4.6.F1.1 audit) — отсутствие ключа
#: значит `UNSUPPORTED_FOR_NLI`, не «забыли добавить».
_VERBALIZERS = {
    ("involves", "ATOM", "ENTITY"): _verbalize_involves,
    ("located_at", "ATOM", "ENTITY"): _verbalize_located_at,
    ("about", "ATOM", "ENTITY"): _verbalize_about_entity,
    ("reason_for", "ATOM", "ATOM"): _verbalize_reason_for,
    ("resulted_in", "ATOM", "ATOM"): _verbalize_resulted_in,
    ("supports", "ATOM", "ATOM"): _verbalize_supports,
    ("related_to", "ENTITY", "ENTITY"): _verbalize_related_to_symmetric,
}


def verbalize(relation_type: str, source: Node, target: Node) -> str:
    """Возвращает natural-language hypothesis или `UNSUPPORTED_FOR_NLI`,
    если для этой (relation_type, source.category, target.category)
    комбинации нет проверенного verbalizer'а — владелец п.4: не
    форсировать плохую строку ради покрытия enum."""
    fn = _VERBALIZERS.get((relation_type, source.category, target.category))
    if fn is None:
        return UNSUPPORTED_FOR_NLI
    return fn(source, target)
