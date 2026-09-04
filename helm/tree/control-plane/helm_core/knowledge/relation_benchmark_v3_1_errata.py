"""R4.7 v3.1 ERRATA (владелец 04.09.2026) — ТОЛЬКО 3 исправления
gold-labeling в уже существующих кейсах `RELATION_BENCHMARK_V3_CASES`
(`relation_benchmark_v3_fixtures.py`, ЗАМОРОЖЕН, остаётся байт-в-байт
неизменным — этот файл его не импортирует для правки, только для
применения поверх).

Контекст (владелец, дословно): «Frozen v3 оставить байт-в-байт
неизменным как исторический артефакт... Если все 5 подтверждаются без
изменения текста/онтологии/правил компилятора — создать v3.1 ERRATA,
содержащий только эти 3 исправления gold. Никаких новых cases, text,
rules или tuning.»

5 пропущенных `entailed`-кортежей в 3 кейсах (полная таблица с evidence
и обоснованием — в отчёте R4.7 в `docs/KNOWLEDGE_MODELS.md`, раздел v3.1):
каждый — конструкция, ТЕКСТУАЛЬНО идентичная уже объявленной entailed
паре в ТОМ ЖЕ кейсе (например `v3_finance_concepts` объявляет `a1 about
e1` за «Статья была посвящена теме дефляции», но не `a2/a3/a5 about e1`
за буквально то же слово «дефляция» как прямое содержание тех же по
конструкции предложений) — не новое толкование, не новый текст, не
новое правило компилятора.

`V3_1_ERRATA` — единственный источник правки, применяемый функцией
`apply_v3_1_errata()` НЕ мутируя исходные `RelationCaseV3` (frozen
dataclass, mutation невозможна физически) — она строит НОВЫЙ tuple
кейсов, подменяя только `entailed` у трёх задетых case_id, остальные —
тот же объект без копирования."""

from __future__ import annotations

from .relation_benchmark_v3_fixtures import RELATION_BENCHMARK_V3_CASES, RelationCaseV3, RelPositive

#: case_id -> кортеж ДОПОЛНИТЕЛЬНЫХ entailed-пар (не замена, а добавка
#: к уже объявленным в frozen v3 для этого же case_id).
V3_1_ERRATA: dict[str, tuple[RelPositive, ...]] = {
    "v3_clinic_visit_specialty": (
        RelPositive("a3", "involves", "e5"),
    ),
    "v3_project_meeting_full": (
        RelPositive("a2", "involves", "e1"),
    ),
    "v3_finance_concepts": (
        RelPositive("a2", "about", "e1"),
        RelPositive("a3", "about", "e1"),
        RelPositive("a5", "about", "e1"),
    ),
}

#: Зафиксировано владельцем: ровно 3 case_id правятся, 5 кортежей
#: добавляются суммарно (1 + 1 + 3) — см. module docstring про
#: расхождение "5 FP / 3 fixes", которое было ошибкой счёта агента
#: (случаи vs кортежи), не реальным расхождением в данных.
assert sum(len(v) for v in V3_1_ERRATA.values()) == 5
assert len(V3_1_ERRATA) == 3


def apply_v3_1_errata(
    cases: tuple[RelationCaseV3, ...] = RELATION_BENCHMARK_V3_CASES,
) -> tuple[RelationCaseV3, ...]:
    """Возвращает НОВЫЙ tuple кейсов с добавленными `V3_1_ERRATA` —
    исходный `cases` (и объекты в нём) не изменяется. Не new-, не
    text-, не rule-tuning: единственная мутация — `entailed` тех 3
    case_id, перечисленных в `V3_1_ERRATA`, расширенный дополнительными
    кортежами оттуда."""
    patched = []
    seen_case_ids: set[str] = set()
    for case in cases:
        seen_case_ids.add(case.case_id)
        extra = V3_1_ERRATA.get(case.case_id)
        if extra is None:
            patched.append(case)
            continue
        for tup in extra:
            if tup in case.entailed:
                raise ValueError(f"{case.case_id}: errata tuple {tup} already entailed in frozen v3")
        patched.append(RelationCaseV3(
            case_id=case.case_id, split=case.split, domain=case.domain, text=case.text,
            entities=case.entities, atoms=case.atoms,
            entailed=case.entailed + extra, not_entailed=case.not_entailed, notes=case.notes,
        ))
    missing = set(V3_1_ERRATA) - seen_case_ids
    if missing:
        raise ValueError(f"v3.1 errata references unknown case_id(s): {missing}")
    return tuple(patched)


RELATION_BENCHMARK_V3_1_CASES: tuple[RelationCaseV3, ...] = apply_v3_1_errata()
