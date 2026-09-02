"""R4 (§14.18) — целостность golden fixtures как данных, до любого запуска
модели. Ловит опечатку в `ref` фикстуры раньше живого прогона на VPS, где
бы она молча дала «связь не найдена» и испортила метрики кандидата."""

from __future__ import annotations

import pytest

from helm_core.knowledge.semantic_benchmark_fixtures import (
    GOLDEN_CASES, REQUIRED_CATEGORIES,
)
from helm_core.models.base import SemanticRelationType


def test_case_ids_are_unique():
    ids = [c.case_id for c in GOLDEN_CASES]
    assert len(ids) == len(set(ids))


def test_every_required_category_has_a_fixture():
    covered = {cat for case in GOLDEN_CASES for cat in case.categories}
    missing = REQUIRED_CATEGORIES - covered
    assert not missing, f"категории без фикстуры: {sorted(missing)}"


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c.case_id)
def test_edge_refs_resolve_to_declared_entities_or_atoms(case):
    known_refs = {e.ref for e in case.entities} | {a.ref for a in case.atoms}
    for edge in case.edges:
        assert edge.from_ref in known_refs, f"{case.case_id}: from_ref {edge.from_ref!r} не объявлен"
        assert edge.to_ref in known_refs, f"{case.case_id}: to_ref {edge.to_ref!r} не объявлен"
        assert edge.relation_type in {m.value for m in SemanticRelationType}, (
            f"{case.case_id}: тип связи {edge.relation_type!r} вне реестра")
    for forbidden in case.forbidden_edges:
        assert forbidden.from_ref in known_refs
        assert forbidden.to_ref in known_refs


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c.case_id)
def test_refs_are_unique_within_a_case(case):
    refs = [e.ref for e in case.entities] + [a.ref for a in case.atoms]
    assert len(refs) == len(set(refs)), f"{case.case_id}: повтор ref"


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c.case_id)
def test_no_knowledge_cases_declare_nothing_else(case):
    if case.expect_no_knowledge:
        assert not case.entities and not case.atoms and not case.edges


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c.case_id)
def test_atom_kinds_are_in_the_closed_registry(case):
    for atom in case.atoms:
        assert atom.kind in {"event", "fact", "decision", "concept"}, (
            f"{case.case_id}: {atom.ref} вид {atom.kind!r} вне реестра атомов")
