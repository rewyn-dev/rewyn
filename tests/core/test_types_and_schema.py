from __future__ import annotations

from pydantic import BaseModel

from rewyn.core.schema import (
    canonical_json,
    fingerprint,
    schema_for_callable,
    schema_for_type,
    validate_json,
)
from rewyn.core.types import new_id, ulid, utcnow


def test_ulid_is_sortable_and_unique() -> None:
    first, second = ulid(), ulid()
    assert len(first) == 26
    assert first != second
    assert first[:10] <= second[:10]  # timestamp prefix is monotonic


def test_new_id_has_prefix() -> None:
    assert new_id("run").startswith("run_")


def test_utcnow_is_aware() -> None:
    assert utcnow().tzinfo is not None


def test_canonical_json_is_deterministic() -> None:
    a = canonical_json({"b": 1, "a": [1, 2, {"z": None, "y": "x"}]})
    b = canonical_json({"a": [1, 2, {"y": "x", "z": None}], "b": 1})
    assert a == b == '{"a":[1,2,{"y":"x","z":null}],"b":1}'


def test_fingerprint_changes_with_content() -> None:
    assert fingerprint({"a": 1}) == fingerprint({"a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})
    assert fingerprint({"a": 1}).startswith("sha256:")


def test_schema_for_callable_reflects_signature() -> None:
    def get_customer(customer_id: str, include_orders: bool = False) -> None: ...

    schema = schema_for_callable(get_customer)
    assert schema["type"] == "object"
    assert schema["properties"]["customer_id"] == {"type": "string"}
    assert schema["properties"]["include_orders"] == {"type": "boolean", "default": False}
    assert schema["required"] == ["customer_id"]
    assert schema["additionalProperties"] is False


def test_schema_for_type_and_validate_json() -> None:
    class Answer(BaseModel):
        value: int
        note: str = ""

    schema = schema_for_type(Answer)
    assert validate_json(schema, {"value": 3}) == []
    errors = validate_json(schema, {"value": "three"})
    assert errors
    assert errors[0].startswith("value:")
