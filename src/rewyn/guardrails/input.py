"""Input guardrail presets (spec §25: PII, prompt injection, prohibited content, policy)."""

from __future__ import annotations

from collections.abc import Iterable

from rewyn.guardrails.policy import Guardrail
from rewyn.guardrails.validators import (
    LengthGuardrail,
    PIIGuardrail,
    ProhibitedContentGuardrail,
    PromptInjectionGuardrail,
)


def input_guardrails(
    *,
    pii: bool | str = False,
    injection: bool | str = True,
    prohibited: Iterable[str] = (),
    max_chars: int | None = None,
) -> list[Guardrail]:
    """Common input guardrails. String values select the action (``block``/``flag``/``redact``)."""
    guardrails: list[Guardrail] = []
    if injection:
        action = "block" if injection is True else injection
        guardrails.append(PromptInjectionGuardrail(action=action, stage="input"))  # type: ignore[arg-type]
    if pii:
        action = "redact" if pii is True else pii
        guardrails.append(PIIGuardrail(action=action, stage="input"))  # type: ignore[arg-type]
    terms = list(prohibited)
    if terms:
        guardrails.append(ProhibitedContentGuardrail(terms, stage="input"))
    if max_chars is not None:
        guardrails.append(LengthGuardrail(max_chars, stage="input"))
    return guardrails
