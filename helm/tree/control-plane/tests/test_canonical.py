"""Канонизация payload — основа хэша действия (§8.3)."""

from decimal import Decimal

import pytest

from helm_core.actions.canonical import NonCanonicalPayload, action_hash, canonical_bytes


def test_key_order_does_not_change_hash():
    assert action_hash("a", {"x": 1, "y": 2}) == action_hash("a", {"y": 2, "x": 1})


def test_action_type_is_part_of_hash():
    """Иначе assertion для безобидного действия подошла бы к опасному."""
    assert action_hash("create_internal_draft", {"x": 1}) != action_hash("merge_main", {"x": 1})


def test_type_cannot_bleed_into_payload():
    """Разделитель не даёт подобрать пару (тип, payload) с тем же хэшем."""
    assert action_hash("ab", {"": ""}) != action_hash("a", {"b": ""})


def test_float_is_rejected():
    with pytest.raises(NonCanonicalPayload, match="float"):
        action_hash("spend_money", {"amount": 0.1 + 0.2})


def test_decimal_is_normalized():
    assert action_hash("spend_money", {"amount": Decimal("1.50")}) == \
           action_hash("spend_money", {"amount": Decimal("1.5")})


def test_non_string_key_rejected():
    with pytest.raises(NonCanonicalPayload):
        action_hash("a", {1: "x"})


def test_unicode_is_not_escaped():
    assert canonical_bytes({"k": "публикация"}) == '{"k":"публикация"}'.encode("utf-8")


def test_nested_structures_are_canonical():
    left = {"a": [{"z": 1, "y": 2}], "b": {"d": None, "c": True}}
    right = {"b": {"c": True, "d": None}, "a": [{"y": 2, "z": 1}]}
    assert action_hash("t", left) == action_hash("t", right)
