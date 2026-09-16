"""Canonical JSON, fingerprints and JSON Schema helpers."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from typing import Any, get_type_hints

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, create_model
from pydantic_core import to_jsonable_python

from rewyn.core.types import JSONObject


def to_jsonable(value: Any) -> Any:
    """Convert arbitrary Python objects to JSON-compatible data.

    Pydantic models, dataclasses, datetimes, enums, paths and sets are handled;
    anything else falls back to ``str``.
    """
    return to_jsonable_python(value, fallback=str, serialize_unknown=True)


def canonical_json(value: Any) -> str:
    """Deterministic JSON encoding: sorted keys, compact separators, UTF-8."""
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value: Any) -> str:
    """Content fingerprint of any JSON-able value (``sha256:<hex>``)."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def short_fingerprint(value: Any) -> str:
    """First 12 hex characters of :func:`fingerprint`, for display."""
    return fingerprint(value).removeprefix("sha256:")[:12]


def _strip_titles(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_strip_titles(item) for item in schema]
    return schema


def schema_for_type(type_: Any) -> JSONObject:
    """JSON Schema for a Python type (pydantic model, dataclass, TypedDict, primitive)."""
    if inspect.isclass(type_) and issubclass(type_, BaseModel):
        raw = type_.model_json_schema()
    else:
        raw = TypeAdapter(type_).json_schema()
    schema: JSONObject = _strip_titles(raw)
    return schema


class _StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


def model_for_callable(fn: Callable[..., Any], *, name: str | None = None) -> type[BaseModel]:
    """Build a pydantic model whose fields mirror ``fn``'s parameters.

    ``self``, ``cls``, ``*args`` and ``**kwargs`` are ignored. Parameters
    without annotations are typed ``Any``. Defaults become field defaults.
    """
    signature = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, include_extras=True)
    except (NameError, TypeError):
        hints = {}
    fields: dict[str, Any] = {}
    for param_name, param in signature.parameters.items():
        if param_name in {"self", "cls"} or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        annotation = hints.get(param_name, Any)
        if param.default is inspect.Parameter.empty:
            fields[param_name] = (annotation, Field(...))
        else:
            fields[param_name] = (annotation, Field(default=param.default))
    model_name = name or f"{fn.__name__}_arguments"
    return create_model(model_name, __base__=_StrictConfig, **fields)


def schema_for_callable(fn: Callable[..., Any]) -> JSONObject:
    """JSON Schema describing the arguments of ``fn``."""
    schema: JSONObject = _strip_titles(model_for_callable(fn).model_json_schema())
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


def validate_json(schema: JSONObject, data: Any) -> list[str]:
    """Validate ``data`` against a JSON Schema and return human-readable errors."""
    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)
    errors = []
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in error.path) or "<root>"
        errors.append(f"{path}: {error.message}")
    return errors


def parse_as(type_: Any, data: Any) -> Any:
    """Validate ``data`` into ``type_`` (pydantic model or any annotated type)."""
    if inspect.isclass(type_) and issubclass(type_, BaseModel):
        return type_.model_validate(data)
    return TypeAdapter(type_).validate_python(data)


def format_validation_error(exc: ValidationError) -> list[str]:
    return [
        f"{'/'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
    ]
