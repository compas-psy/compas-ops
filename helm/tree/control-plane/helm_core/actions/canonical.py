"""Каноническая сериализация payload действия и его SHA256.

Хэш действия — единственное, что связывает «владелец одобрил вот это» с
«исполнено вот это» (ТЗ §8.3, §8.4). Поэтому сериализация обязана быть
детерминированной и не иметь двух представлений одного значения.

Подход — строгое подмножество JCS (RFC 8785): сортировка ключей, UTF-8 без
экранирования не-ASCII, разделители без пробелов. Всё, что может
сериализоваться неоднозначно, не принимается вместо того, чтобы
догадываться:

- float отвергается. `0.1 + 0.2` в IEEE754 не равно `0.3`, а суммы денег
  проходят через этот хэш. Дробные значения передаются Decimal или строкой.
- NaN/Infinity отвергаются: у них нет представления в JSON.
- ключи не-строки отвергаются: json.dumps молча привёл бы 1 и "1" к одному
  ключу и два разных payload дали бы один хэш.
"""

import hashlib
import json
from decimal import Decimal
from typing import Any

CANONICAL_ENCODING = "utf-8"


class NonCanonicalPayload(ValueError):
    """Payload содержит значение без однозначного представления."""


def _normalize(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value

    if isinstance(value, int):
        # bool — подкласс int, он уже обработан выше.
        return value

    if isinstance(value, Decimal):
        if not value.is_finite():
            raise NonCanonicalPayload(f"{path}: нефинитный Decimal {value!r}")
        # Нормализованная экспоненциальная форма исключает то, что
        # Decimal("1.50") и Decimal("1.5") дадут разные строки.
        return format(value.normalize(), "f")

    if isinstance(value, float):
        raise NonCanonicalPayload(
            f"{path}: float запрещён в payload действия — используйте Decimal "
            f"или строку (получено {value!r})"
        )

    if isinstance(value, (list, tuple)):
        return [_normalize(item, f"{path}[{i}]") for i, item in enumerate(value)]

    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise NonCanonicalPayload(
                    f"{path}: ключ {key!r} не строка — два разных payload могли бы "
                    f"дать один хэш"
                )
            out[key] = _normalize(item, f"{path}.{key}")
        return out

    raise NonCanonicalPayload(f"{path}: тип {type(value).__name__} не сериализуется канонически")


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Канонические байты payload. Один payload — ровно одно представление."""
    if not isinstance(payload, dict):
        raise NonCanonicalPayload("payload действия должен быть объектом")
    normalized = _normalize(payload)
    return json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode(CANONICAL_ENCODING)


def action_hash(action_type: str, payload: dict[str, Any]) -> str:
    """SHA256 действия: тип и payload вместе.

    Тип входит в хэш, иначе одинаковый payload у `publish_public_content` и
    у `create_internal_draft` дал бы один хэш, и assertion, выданная для
    безобидного действия, подошла бы к опасному.
    """
    if not action_type:
        raise NonCanonicalPayload("action_type пуст")
    digest = hashlib.sha256()
    digest.update(action_type.encode(CANONICAL_ENCODING))
    digest.update(b"\x00")  # разделитель: тип не может «перетечь» в payload
    digest.update(canonical_bytes(payload))
    return digest.hexdigest()
