"""R4.6.F1 (владелец 03.09.2026) — детерминированный typed+directed NLI
dataset builder. Не проверяет качество NLI (это делает сам F1-бенчмарк
на живом сервере) — проверяет контракт: полнота шаблонов на весь
закрытый реестр §14.9, четыре варианта на каждое gold-ребро (когда они
доступны), направление действительно инвертируется, wrong_type
действительно другой тип, false_pair — реальная не-gold пара из ТОГО ЖЕ
кейса или честное отсутствие (не крах), и полная детерминированность
(два вызова — идентичный результат)."""

from __future__ import annotations

from dataclasses import replace

from helm_core.knowledge.nli_relation_dataset import (
    RELATION_HYPOTHESIS_TEMPLATES, RELATION_TYPE_ORDER, build_examples,
)
from helm_core.knowledge.semantic_benchmark_fixtures import GoldEdge, GoldEntity, GoldenCase
from helm_core.models.base import SemanticRelationType


def test_templates_cover_the_full_closed_registry_exactly():
    assert set(RELATION_HYPOTHESIS_TEMPLATES) == {m.value for m in SemanticRelationType}
    assert set(RELATION_TYPE_ORDER) == {m.value for m in SemanticRelationType}


def test_every_template_has_exactly_one_a_and_one_b_placeholder():
    for relation_type, template in RELATION_HYPOTHESIS_TEMPLATES.items():
        assert template.count("{a}") == 1, relation_type
        assert template.count("{b}") == 1, relation_type
        rendered = template.format(a="OBJ_A", b="OBJ_B")
        assert "OBJ_A" in rendered and "OBJ_B" in rendered, relation_type


def test_build_examples_is_fully_deterministic():
    assert build_examples() == build_examples()


CASE = GoldenCase(
    case_id="t1", categories=(), domain="test",
    text="Иванов работает в Ромашке. Волков подписал акт.",
    entities=(
        GoldEntity(ref="e1", entity_type="PERSON", label="Иванов"),
        GoldEntity(ref="e2", entity_type="PERSON", label="Волков"),
        GoldEntity(ref="e3", entity_type="ORGANIZATION", label="Ромашка"),
    ),
    edges=(GoldEdge(from_ref="e1", relation_type="owned_by", to_ref="e3"),),
)


def test_positive_example_uses_correct_template_and_direction():
    examples = build_examples((CASE,))
    positive = next(e for e in examples if e.kind == "positive")
    assert positive.entailed is True
    assert positive.hypothesis == "Иванов принадлежит Ромашка."
    assert positive.from_ref == "e1" and positive.to_ref == "e3"


def test_wrong_type_hard_negative_uses_a_different_type_same_pair():
    examples = build_examples((CASE,))
    wrong = next(e for e in examples if e.kind == "wrong_type")
    assert wrong.entailed is False
    assert wrong.relation_type != "owned_by"
    assert wrong.from_ref == "e1" and wrong.to_ref == "e3"


def test_reversed_direction_hard_negative_swaps_the_labels():
    examples = build_examples((CASE,))
    reversed_ex = next(e for e in examples if e.kind == "reversed_direction")
    assert reversed_ex.entailed is False
    assert reversed_ex.hypothesis == "Ромашка принадлежит Иванов."
    assert reversed_ex.from_ref == "e3" and reversed_ex.to_ref == "e1"


def test_false_pair_hard_negative_is_a_real_non_gold_pair_from_the_same_case():
    examples = build_examples((CASE,))
    false_pair = next(e for e in examples if e.kind == "false_pair")
    assert false_pair.entailed is False
    named_pair = frozenset((false_pair.from_ref, false_pair.to_ref))
    assert named_pair != frozenset(("e1", "e3"))  # не исходная gold-пара
    assert named_pair <= {"e1", "e2", "e3"}  # объекты из ТОГО ЖЕ кейса


def test_false_pair_absent_when_case_has_no_alternative_pair_not_a_crash():
    tiny_case = replace(CASE, entities=(GoldEntity(ref="e1", entity_type="PERSON", label="Иванов"),),
                        edges=(GoldEdge(from_ref="e1", relation_type="related_to", to_ref="e1"),))
    # Даже вырожденный случай (одна сущность) не должен падать — false_pair
    # просто отсутствует в выводе.
    examples = build_examples((tiny_case,))
    assert not any(e.kind == "false_pair" for e in examples)
    assert any(e.kind == "positive" for e in examples)


def test_case_id_is_attached_for_later_case_level_splitting():
    examples = build_examples((CASE,))
    assert all(e.case_id == "t1" for e in examples)


def test_never_leaves_the_machine():
    import ast
    import inspect

    import helm_core.knowledge.nli_relation_dataset as module

    tree = ast.parse(inspect.getsource(module))
    urls = [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "://" in node.value]

    assert urls == [], f"построение датасета не должно знать сетевые адреса: {urls}"
