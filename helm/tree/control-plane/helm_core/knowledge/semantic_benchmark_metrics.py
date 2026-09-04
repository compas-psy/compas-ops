"""R4 (§14.18) — сопоставление golden fixture с ответом модели и метрики.

Владелец прямо потребовал «метрики по полям, не по красоте текста» (R4
п.5). Поэтому здесь НЕТ общей «оценки похожести ответа» — есть отдельные
числа на entity_type, subtype, aliases, kind, дату, тип связи, и отдельно —
счётчики безопасности (выдуманная дата/связь/факт), которые не усредняются
с остальным баллом ни при каких обстоятельствах: R4 п.7 требует, чтобы
материальная галлюцинация была hard gate, а не «минус 0.1 к F1».

local_id — то, что придумывает модель НА ЭТОМ прогоне; сравнивать его с
`ref` фикстуры бессмысленно. Сопоставление идёт по содержимому (похожесть
label/текста), не по идентификатору.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from ..models.base import AUTO_EXTRACTABLE_RELATIONS_V1
from .semantic_benchmark_fixtures import ForbiddenEdge, GoldAtom, GoldEntity, GoldenCase
from .semantic_extract import ExtractedAtom, ExtractedEdge, ExtractedEntity, WindowExtraction

#: R4.7 P6 (владелец 2026-09-04, R4 RCA `lecture_concept`: подтверждённый
#: evaluator-дефект, не fixture-дефект) — production-путь с R4.7 оценивает
#: ИМЕННО compiler-driven extraction (`relation_compiler.py`), а
#: `RELATED_TO` сознательно исключён из `AUTO_EXTRACTABLE_RELATIONS_V1`
#: (owner decision, `models/base.py`): детерминированный compiler
#: структурно не может произвести эту связь ни при каком качестве
#: extraction. Golden-ожидание такого типа не редактируется (текст/
#: сущности фикстуры остаются verbatim), но и не входит в
#: `edges_gold_scoreable`/recall — иначе метрика штрафует compiler за
#: то, что ему запрещено производить.
_AUTO_EXTRACTABLE_RELATION_VALUES = frozenset(rt.value for rt in AUTO_EXTRACTABLE_RELATIONS_V1)

ENTITY_MATCH_THRESHOLD = 0.6
ATOM_MATCH_THRESHOLD = 0.5

#: Категории, на которых материальная галлюцинация — не пункт метрики, а
#: hard gate (R4 п.7: «material hallucination on golden safety cases»).
SAFETY_CATEGORIES = frozenset({
    "no_knowledge", "provocative_no_fact", "provocative_no_relation",
    "provocative_no_date", "negative_statement",
})

_QUOTES = re.compile(r"[«»\"'`]")
_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", _QUOTES.sub("", text).strip().casefold())


def _similarity(a: str, b: str) -> float:
    a, b = _normalize(a), _normalize(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _role_signature(ref_or_id: str, edges, *, to_field: str, role_field: str) -> frozenset[str]:
    sig = set()
    for e in edges:
        if getattr(e, to_field) == ref_or_id:
            role = getattr(e, role_field)
            if role:
                sig.add(_normalize(role))
    return frozenset(sig)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


@dataclass
class Match:
    gold: object
    extracted: object
    score: float


@dataclass
class MatchResult:
    matched: list[Match] = field(default_factory=list)
    unmatched_gold: list = field(default_factory=list)
    unmatched_extracted: list = field(default_factory=list)


def _greedy_match(gold_items, extracted_items, *, threshold: float, composite_score) -> MatchResult:
    """Жадное сопоставление по убыванию составного балла.

    Не венгерский алгоритм — фикстуры маленькие (единицы элементов), а
    жадности с составным баллом (сходство + маленький бонус за контекст)
    достаточно, чтобы развести `same_label_different_entities`, где сходство
    меток само по себе неразличимо (см. docstring _role_signature-бонуса
    в match_entities)."""
    candidates = []
    for gi, g in enumerate(gold_items):
        for ei, e in enumerate(extracted_items):
            score = composite_score(g, e)
            if score >= threshold:
                candidates.append((score, gi, ei))
    candidates.sort(key=lambda c: c[0], reverse=True)

    used_gold: set[int] = set()
    used_extracted: set[int] = set()
    result = MatchResult()
    for score, gi, ei in candidates:
        if gi in used_gold or ei in used_extracted:
            continue
        used_gold.add(gi)
        used_extracted.add(ei)
        result.matched.append(Match(gold=gold_items[gi], extracted=extracted_items[ei], score=score))
    result.unmatched_gold = [g for i, g in enumerate(gold_items) if i not in used_gold]
    result.unmatched_extracted = [e for i, e in enumerate(extracted_items) if i not in used_extracted]
    return result


def match_entities(gold: tuple[GoldEntity, ...], extracted: list[ExtractedEntity]) -> MatchResult:
    def composite(g: GoldEntity, e: ExtractedEntity) -> float:
        label_sim = _similarity(g.label, e.label)
        # Бонус на 3-м знаке — ломает точную ничью (два разных человека с
        # одинаковым label), не может перебить настоящее различие меток.
        g_roles = _role_signature(g.ref, GOLD_EDGES_CTX.get(), to_field="to_ref", role_field="role")
        e_roles = _role_signature(e.local_id, EXTRACTED_EDGES_CTX.get(),
                                  to_field="to_local_id", role_field="role")
        return label_sim + 0.001 * _jaccard(g_roles, e_roles)

    return _greedy_match(list(gold), extracted, threshold=ENTITY_MATCH_THRESHOLD, composite_score=composite)


def match_atoms(gold: tuple[GoldAtom, ...], extracted: list[ExtractedAtom]) -> MatchResult:
    def composite(g: GoldAtom, e: ExtractedAtom) -> float:
        text_sim = _similarity(g.canonical_text, e.text)
        kind_bonus = 0.001 if _normalize(g.kind) == _normalize(e.kind) else 0.0
        return text_sim + kind_bonus

    return _greedy_match(list(gold), extracted, threshold=ATOM_MATCH_THRESHOLD, composite_score=composite)


#: `match_entities` нужен доступ к рёбрам обеих сторон только для бонуса
#: разрешения ничьей — не хочется тащить их третьим/четвёртым параметром
#: через `_greedy_match`, который не должен ничего знать про рёбра. Modul
#: -level контекст, выставляемый `evaluate_case` перед вызовом и снимаемый
#: после — не потокобезопасно, но бенчмарк однопоточный по конструкции
#: (§14.4.3 не требует параллелизма, а параллельные Ollama-вызовы на 8
#: vCPU/12GB — сам по себе риск, которого R4 избегает).
class _Ctx:
    def __init__(self):
        self._value = ()

    def set(self, value):
        self._value = value

    def get(self):
        return self._value


GOLD_EDGES_CTX = _Ctx()
EXTRACTED_EDGES_CTX = _Ctx()


@dataclass
class CaseScore:
    case_id: str
    categories: tuple[str, ...]
    is_safety_case: bool

    entities_gold: int = 0
    entities_matched: int = 0
    entities_extracted_extra: int = 0
    entity_type_correct: int = 0
    subtype_correct: int = 0
    subtype_applicable: int = 0
    aliases_correct: int = 0
    aliases_applicable: int = 0

    #: §14.18 hard gate «critical expected entity/event recall» — подмножество
    #: entities_gold/matched, где GoldEntity.critical истинен (см. фикстуры).
    #: НЕ общий entity_recall: CONCEPT-сущности туда не входят намеренно.
    critical_entities_gold: int = 0
    critical_entities_matched: int = 0

    atoms_gold: int = 0
    atoms_matched: int = 0
    atoms_extracted_extra: int = 0
    atom_kind_correct: int = 0
    date_correct: int = 0
    date_applicable: int = 0

    #: Та же критичность, что у entities выше, но для атомов: GoldAtom.critical
    #: (EVENT-kind либо датированный атом) — часть того же combined
    #: critical_entity_event_recall гейта, не отдельная метрика.
    critical_atoms_gold: int = 0
    critical_atoms_matched: int = 0

    #: §14.18: «identifier/date corruption on exact fixture» — matched-по-
    #: похожести объект, чьё значение (label/occurred_at) РАСХОДИТСЯ с gold
    #: буквально. Не то же самое, что miss (recall) — объект найден, но
    #: испорчен, и именно это отдельный hard gate, а не minus к F1.
    exact_identifier_corruptions: int = 0
    exact_date_corruptions: int = 0

    edges_gold_scoreable: int = 0
    edges_matched: int = 0
    edges_extracted_extra: int = 0
    relation_type_correct: int = 0

    fabricated_dates: int = 0
    fabricated_relations: int = 0
    inverted_negations: int = 0
    unsupported_fact_additions: int = 0
    no_knowledge_violation: bool = False

    rejected_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def material_hallucinations(self) -> int:
        """R4 п.7: material hallucination — не сумма минусов, а флаг per
        case. Считаем количество независимых нарушений, каждое отдельно
        репортируется, но для hard gate важен только факт > 0."""
        return (self.fabricated_dates + self.fabricated_relations
                + self.inverted_negations + self.unsupported_fact_additions
                + (1 if self.no_knowledge_violation else 0))


def evaluate_case(case: GoldenCase, extraction: WindowExtraction) -> CaseScore:
    score = CaseScore(case_id=case.case_id, categories=case.categories,
                      is_safety_case=bool(SAFETY_CATEGORIES & set(case.categories)),
                      rejected_count=len(extraction.rejected))

    if case.expect_no_knowledge:
        if extraction.entities or extraction.atoms:
            score.no_knowledge_violation = True
            score.notes.append(
                f"NO_KNOWLEDGE придумал {len(extraction.entities)} сущностей и "
                f"{len(extraction.atoms)} атомов")
        return score

    GOLD_EDGES_CTX.set(case.edges)
    EXTRACTED_EDGES_CTX.set(extraction.edges)
    try:
        entity_match = match_entities(case.entities, extraction.entities)
        atom_match = match_atoms(case.atoms, extraction.atoms)
    finally:
        GOLD_EDGES_CTX.set(())
        EXTRACTED_EDGES_CTX.set(())

    score.entities_gold = len(case.entities)
    score.entities_matched = len(entity_match.matched)
    score.entities_extracted_extra = len(entity_match.unmatched_extracted)
    score.critical_entities_gold = sum(1 for g in case.entities if g.critical)

    ref_to_local_id: dict[str, str] = {}
    for m in entity_match.matched:
        g: GoldEntity = m.gold
        e: ExtractedEntity = m.extracted
        ref_to_local_id[g.ref] = e.local_id
        if g.critical:
            score.critical_entities_matched += 1
            if _normalize(g.label) != _normalize(e.label):
                score.exact_identifier_corruptions += 1
                score.notes.append(
                    f"{g.ref}: identifier corruption — gold={g.label!r} extracted={e.label!r}")
        if _normalize(g.entity_type) == _normalize(e.entity_type):
            score.entity_type_correct += 1
        if g.subtype is not None:
            score.subtype_applicable += 1
            if e.subtype and _similarity(g.subtype, e.subtype) >= 0.6:
                score.subtype_correct += 1
        if g.aliases:
            score.aliases_applicable += 1
            gold_aliases = {_normalize(a) for a in g.aliases}
            ext_aliases = {_normalize(a) for a in e.aliases}
            if gold_aliases and gold_aliases <= ext_aliases:
                score.aliases_correct += 1

    # provocative_no_fact: у сущности не должно появиться атома СВЕРХ того,
    # что прямо разрешил gold (иногда это 0 атомов, иногда — ровно один
    # дозволенный факт, как «значится в списке участников»). Владелец
    # 03.09.2026: старая версия проверки требовала строго 0 атомов и
    # штрафовала модель за факт, который сам gold-текст утверждает —
    # generalized-версия проверяет «атом сверх gold», а не «атом вообще».
    if "provocative_no_fact" in case.categories:
        matched_entity_local_ids = set(ref_to_local_id.values())
        extra_atom_local_ids = {a.local_id for a in atom_match.unmatched_extracted}
        for e in extraction.edges:
            touches_entity = e.to_local_id in matched_entity_local_ids \
                or e.from_local_id in matched_entity_local_ids
            touches_extra_atom = e.to_local_id in extra_atom_local_ids \
                or e.from_local_id in extra_atom_local_ids
            if touches_entity and touches_extra_atom:
                score.unsupported_fact_additions += 1
                score.notes.append(f"выдуман факт про сущность сверх дозволенного gold: {e}")

    score.atoms_gold = len(case.atoms)
    score.atoms_matched = len(atom_match.matched)
    score.atoms_extracted_extra = len(atom_match.unmatched_extracted)
    score.critical_atoms_gold = sum(1 for g in case.atoms if g.critical)

    for m in atom_match.matched:
        g: GoldAtom = m.gold
        e: ExtractedAtom = m.extracted
        ref_to_local_id[g.ref] = e.local_id
        if g.critical:
            score.critical_atoms_matched += 1
        if _normalize(g.kind) == _normalize(e.kind):
            score.atom_kind_correct += 1

        if g.date_precision is None:
            if e.occurred_at:
                score.fabricated_dates += 1
                score.notes.append(f"{g.ref}: дата придумана там, где в тексте её нет: {e.occurred_at!r}")
        else:
            score.date_applicable += 1
            if g.date_precision == "unknown":
                if e.date_precision == "unknown" and not e.occurred_at:
                    score.date_correct += 1
                elif e.occurred_at:
                    score.fabricated_dates += 1
                    score.notes.append(
                        f"{g.ref}: точная дата придумана для неразрешимой ссылки: {e.occurred_at!r}")
            else:
                if e.date_precision == g.date_precision and e.occurred_at == g.occurred_at:
                    score.date_correct += 1
                elif e.occurred_at and g.occurred_at and e.occurred_at != g.occurred_at:
                    # §14.18 «identifier/date corruption on exact fixture»:
                    # атом найден (matched по тексту), но точная дата
                    # разошлась с gold — не miss (recall), а порча значения.
                    score.exact_date_corruptions += 1
                    score.notes.append(
                        f"{g.ref}: точная дата расходится с gold — "
                        f"gold={g.occurred_at!r} extracted={e.occurred_at!r}")

        if g.negation_sensitive:
            has_negation = bool(re.search(r"\bне\b|\bнет\b|\bнельзя\b", e.text, flags=re.IGNORECASE))
            if not has_negation:
                score.inverted_negations += 1
                score.notes.append(f"{g.ref}: потеряно отрицание в «{e.text}»")

    gold_edge_index = {(edge.from_ref, edge.to_ref): edge for edge in case.edges}
    scoreable_gold_edges = [
        edge for edge in case.edges
        if edge.from_ref in ref_to_local_id and edge.to_ref in ref_to_local_id
        and edge.relation_type in _AUTO_EXTRACTABLE_RELATION_VALUES
    ]
    score.edges_gold_scoreable = len(scoreable_gold_edges)

    extracted_edge_set = {(e.from_local_id, e.to_local_id): e for e in extraction.edges}
    matched_extracted_keys: set[tuple[str, str]] = set()
    for edge in scoreable_gold_edges:
        key = (ref_to_local_id[edge.from_ref], ref_to_local_id[edge.to_ref])
        ext = extracted_edge_set.get(key)
        if ext is not None:
            score.edges_matched += 1
            matched_extracted_keys.add(key)
            if _normalize(ext.relation_type) == _normalize(edge.relation_type):
                score.relation_type_correct += 1
        else:
            score.notes.append(f"связь не найдена: {edge.from_ref} {edge.relation_type} {edge.to_ref}")
    score.edges_extracted_extra = len(extracted_edge_set) - len(matched_extracted_keys)

    for forbidden in case.forbidden_edges:
        if forbidden.from_ref not in ref_to_local_id or forbidden.to_ref not in ref_to_local_id:
            continue
        key = (ref_to_local_id[forbidden.from_ref], ref_to_local_id[forbidden.to_ref])
        if key in extracted_edge_set:
            score.fabricated_relations += 1
            score.notes.append(f"выдумана запрещённая связь {forbidden.from_ref} → {forbidden.to_ref}")

    return score


@dataclass
class AggregateMetrics:
    cases_scored: int = 0
    entity_precision: float = 0.0
    entity_recall: float = 0.0
    entity_f1: float = 0.0
    entity_type_accuracy: float = 0.0
    subtype_accuracy: float = 0.0
    aliases_accuracy: float = 0.0
    atom_precision: float = 0.0
    atom_recall: float = 0.0
    atom_f1: float = 0.0
    atom_kind_accuracy: float = 0.0
    date_accuracy: float = 0.0
    #: Владелец 03.09.2026 (R4.6.B.1): нормативный §14.18 «relation
    #: precision on labeled edges» — TYPED идентичность связи `(from,
    #: normalized_type, to)`, не только пара конечных точек. Верная пара
    #: с неверным типом — НЕ typed match: связь была извлечена, значит
    #: остаётся среди извлечённого и уходит в `extra` (была связь — она
    #: неверна), а не пропадает бесследно и не засчитывается почти-верной.
    #: `endpoint_relation_precision`/`_recall` ниже — старое (endpoint-
    #: only) измерение, оставлено ДИАГНОСТИЧЕСКИ: разводит «не нашёл пару»
    #: от «нашёл пару, перепутал тип» — но не гейт и не участвует в отборе.
    relation_precision: float = 0.0
    relation_recall: float = 0.0
    relation_f1: float = 0.0
    relation_type_accuracy: float = 0.0
    endpoint_relation_precision: float = 0.0
    endpoint_relation_recall: float = 0.0
    #: Владелец 03.09.2026 (R4.6.B.1 п.2): защита от «идеальная точность
    #: через молчание» — НЕ полагается на то, что `_safe_div(0, 0)` уже
    #: сегодня возвращает 0.0, а не 1.0. `evaluate_hard_gates()` явно
    #: проверяет `relation_gold_scoreable > 0 and relation_extracted_total
    #: == 0` как отдельное условие, не производное от самой precision —
    #: иначе будущая правка `_safe_div()` («0 попыток — 0 провалов, значит
    #: вакуумно точно») тихо открыла бы дыру в гейте.
    relation_gold_scoreable: int = 0
    relation_extracted_total: int = 0
    no_knowledge_violations: int = 0
    fabricated_dates: int = 0
    fabricated_relations: int = 0
    inverted_negations: int = 0
    unsupported_fact_additions: int = 0
    total_material_hallucinations: int = 0
    safety_case_hallucinations: int = 0
    rejected_items_total: int = 0
    per_category: dict = field(default_factory=dict)

    #: §14.18 нормативные hard gates (владелец 03.09.2026, R4.5.6 —
    #: восстановление после drift от спеки). НЕ дубли существующих полей
    #: выше по случайности: critical_entity_event_recall — подмножество
    #: GoldEntity.critical/GoldAtom.critical, не общий entity/atom recall;
    #: exact_identifier/date_corruption — matched-объект с расходящимся
    #: значением, отдельно от recall (объект НАЙДЕН, но испорчен).
    critical_entity_event_recall: float = 0.0
    exact_identifier_corruption: int = 0
    exact_date_corruption: int = 0

    @property
    def unsupported_critical_facts(self) -> int:
        """§14.18 gate name для того же измерения, что и
        `total_material_hallucinations` — property, а не отдельное поле:
        AggregateMetrics иногда конструируют напрямую (тесты), а не только
        через `aggregate()`, и хранить одно и то же число в двух полях
        значило бы держать их синхронными вручную — тот же класс бага,
        что и `peak_rss_mb=None → 0.0` (ретракция владельца 02.09.2026)."""
        return self.total_material_hallucinations


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def aggregate(scores: list[CaseScore]) -> AggregateMetrics:
    agg = AggregateMetrics(cases_scored=len(scores))

    entities_gold = sum(s.entities_gold for s in scores)
    entities_matched = sum(s.entities_matched for s in scores)
    entities_extra = sum(s.entities_extracted_extra for s in scores)
    agg.entity_recall = _safe_div(entities_matched, entities_gold)
    agg.entity_precision = _safe_div(entities_matched, entities_matched + entities_extra)
    agg.entity_f1 = _safe_div(2 * agg.entity_precision * agg.entity_recall,
                              agg.entity_precision + agg.entity_recall) \
        if (agg.entity_precision + agg.entity_recall) else 0.0
    agg.entity_type_accuracy = _safe_div(sum(s.entity_type_correct for s in scores), entities_matched)
    agg.subtype_accuracy = _safe_div(sum(s.subtype_correct for s in scores),
                                     sum(s.subtype_applicable for s in scores))
    agg.aliases_accuracy = _safe_div(sum(s.aliases_correct for s in scores),
                                     sum(s.aliases_applicable for s in scores))

    atoms_gold = sum(s.atoms_gold for s in scores)
    atoms_matched = sum(s.atoms_matched for s in scores)
    atoms_extra = sum(s.atoms_extracted_extra for s in scores)
    agg.atom_recall = _safe_div(atoms_matched, atoms_gold)
    agg.atom_precision = _safe_div(atoms_matched, atoms_matched + atoms_extra)
    agg.atom_f1 = _safe_div(2 * agg.atom_precision * agg.atom_recall,
                            agg.atom_precision + agg.atom_recall) \
        if (agg.atom_precision + agg.atom_recall) else 0.0
    agg.atom_kind_accuracy = _safe_div(sum(s.atom_kind_correct for s in scores), atoms_matched)
    agg.date_accuracy = _safe_div(sum(s.date_correct for s in scores),
                                  sum(s.date_applicable for s in scores))

    edges_scoreable = sum(s.edges_gold_scoreable for s in scores)
    edges_matched = sum(s.edges_matched for s in scores)          # endpoint-only (from, to)
    edges_extra = sum(s.edges_extracted_extra for s in scores)    # endpoint-only
    relation_type_correct = sum(s.relation_type_correct for s in scores)

    agg.relation_gold_scoreable = edges_scoreable
    agg.relation_extracted_total = edges_matched + edges_extra
    agg.endpoint_relation_recall = _safe_div(edges_matched, edges_scoreable)
    agg.endpoint_relation_precision = _safe_div(edges_matched, edges_matched + edges_extra)
    agg.relation_type_accuracy = _safe_div(relation_type_correct, edges_matched)

    # Владелец 03.09.2026 (R4.6.B.1) — нормативная relation_precision:
    # typed идентичность (from, type, to). Верная пара с неверным типом
    # НЕ typed-matched — она уже извлечена, значит переходит в typed
    # extra, а не исчезает из обоих знаменателей молча.
    typed_matched = relation_type_correct
    typed_extra = edges_extra + (edges_matched - relation_type_correct)
    agg.relation_precision = _safe_div(typed_matched, typed_matched + typed_extra)
    agg.relation_recall = _safe_div(typed_matched, edges_scoreable)
    agg.relation_f1 = _safe_div(2 * agg.relation_precision * agg.relation_recall,
                                agg.relation_precision + agg.relation_recall) \
        if (agg.relation_precision + agg.relation_recall) else 0.0

    agg.no_knowledge_violations = sum(1 for s in scores if s.no_knowledge_violation)
    agg.fabricated_dates = sum(s.fabricated_dates for s in scores)
    agg.fabricated_relations = sum(s.fabricated_relations for s in scores)
    agg.inverted_negations = sum(s.inverted_negations for s in scores)
    agg.unsupported_fact_additions = sum(s.unsupported_fact_additions for s in scores)
    agg.total_material_hallucinations = sum(s.material_hallucinations for s in scores)
    agg.safety_case_hallucinations = sum(s.material_hallucinations for s in scores if s.is_safety_case)
    agg.rejected_items_total = sum(s.rejected_count for s in scores)

    critical_gold = (sum(s.critical_entities_gold for s in scores)
                     + sum(s.critical_atoms_gold for s in scores))
    critical_matched = (sum(s.critical_entities_matched for s in scores)
                        + sum(s.critical_atoms_matched for s in scores))
    agg.critical_entity_event_recall = _safe_div(critical_matched, critical_gold)
    agg.exact_identifier_corruption = sum(s.exact_identifier_corruptions for s in scores)
    agg.exact_date_corruption = sum(s.exact_date_corruptions for s in scores)

    categories: dict[str, list[CaseScore]] = {}
    for s in scores:
        for cat in s.categories:
            categories.setdefault(cat, []).append(s)
    agg.per_category = {
        cat: {
            "cases": len(cat_scores),
            "material_hallucinations": sum(cs.material_hallucinations for cs in cat_scores),
        }
        for cat, cat_scores in categories.items()
    }
    return agg
