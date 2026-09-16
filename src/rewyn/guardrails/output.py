"""Output guardrail presets (spec §25: schema, policy, factuality, safety, sensitive info)."""

from __future__ import annotations

from collections.abc import Iterable

from rewyn.core.types import JSONObject
from rewyn.guardrails.policy import Guardrail
from rewyn.guardrails.validators import (
    LengthGuardrail,
    ModelGuardrail,
    PIIGuardrail,
    ProhibitedContentGuardrail,
    SchemaGuardrail,
)
from rewyn.models.base import Model


def output_guardrails(
    *,
    schema: JSONObject | None = None,
    sensitive: bool | str = True,
    prohibited: Iterable[str] = (),
    max_chars: int | None = None,
    policy: str | None = None,
    policy_model: Model | None = None,
) -> list[Guardrail]:
    guardrails: list[Guardrail] = []
    if schema is not None:
        guardrails.append(SchemaGuardrail(schema))
    if sensitive:
        action = "redact" if sensitive is True else sensitive
        guardrails.append(PIIGuardrail(action=action, stage="output"))  # type: ignore[arg-type]
    terms = list(prohibited)
    if terms:
        guardrails.append(ProhibitedContentGuardrail(terms, stage="output"))
    if max_chars is not None:
        guardrails.append(LengthGuardrail(max_chars, stage="output"))
    if policy is not None:
        if policy_model is None:
            raise ValueError("policy guardrails need policy_model")
        guardrails.append(ModelGuardrail(policy_model, policy, stage="output"))
    return guardrails
