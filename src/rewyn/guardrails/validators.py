"""Built-in guardrails: PII, prompt injection, prohibited content, schema, length, model-judged."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from re import Pattern
from typing import Any, Literal

from rewyn.core.schema import validate_json
from rewyn.core.types import JSONObject
from rewyn.guardrails.policy import Action, GuardrailDecision, Stage
from rewyn.models.base import Model, extract_json
from rewyn.security.redaction import Redactor

_INJECTION_PATTERNS: tuple[Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (?:all|any|the)? ?(?:previous|prior|above|earlier) (?:instructions|prompts|rules)",
        r"disregard (?:all|any|the)? ?(?:previous|prior|above) (?:instructions|rules)",
        r"you are now (?:in )?(?:developer|dan|jailbreak|unrestricted) mode",
        r"reveal (?:your|the) (?:system|hidden|secret) prompt",
        r"(?:print|show|repeat) (?:your|the) (?:system prompt|instructions) (?:verbatim|exactly)",
        r"<\s*/?\s*system\s*>",
        r"\bBEGIN (?:SYSTEM|ADMIN) (?:PROMPT|OVERRIDE)\b",
    )
)


class PIIGuardrail:
    """Redact (default) or block text containing PII."""

    def __init__(
        self, *, action: Literal["redact", "block", "flag"] = "redact", stage: Stage = "both"
    ) -> None:
        self.name = "pii"
        self.stage = stage
        self.action: Action = action
        self._redactor = Redactor(redact_pii=True)

    async def check(self, text: str, *, context: JSONObject) -> GuardrailDecision:
        cleaned = self._redactor.redact_text(text)
        if cleaned == text:
            return GuardrailDecision.allow(self.name)
        count = cleaned.count("[REDACTED]") - text.count("[REDACTED]")
        if self.action == "redact":
            return GuardrailDecision.redact(
                self.name, cleaned, f"redacted {count} PII item(s)", count=count
            )
        if self.action == "block":
            return GuardrailDecision.block(
                self.name, f"text contains {count} PII item(s)", count=count
            )
        return GuardrailDecision.flag(self.name, f"text contains {count} PII item(s)", count=count)


class PromptInjectionGuardrail:
    """Heuristic detection of instruction-override attempts (not a guarantee)."""

    def __init__(
        self,
        *,
        action: Literal["block", "flag"] = "block",
        stage: Stage = "input",
        extra_patterns: Iterable[str] = (),
    ) -> None:
        self.name = "prompt_injection"
        self.stage = stage
        self.action: Action = action
        self.patterns = list(_INJECTION_PATTERNS) + [
            re.compile(p, re.IGNORECASE) for p in extra_patterns
        ]

    async def check(self, text: str, *, context: JSONObject) -> GuardrailDecision:
        matches = [p.pattern for p in self.patterns if p.search(text)]
        if not matches:
            return GuardrailDecision.allow(self.name)
        reason = f"possible prompt injection ({len(matches)} pattern match(es))"
        if self.action == "block":
            return GuardrailDecision.block(self.name, reason, patterns=matches)
        return GuardrailDecision.flag(self.name, reason, patterns=matches)


class ProhibitedContentGuardrail:
    def __init__(
        self,
        terms: Iterable[str] = (),
        *,
        patterns: Iterable[str] = (),
        action: Literal["block", "redact", "flag"] = "block",
        stage: Stage = "both",
        name: str = "prohibited_content",
    ) -> None:
        self.name = name
        self.stage = stage
        self.action: Action = action
        self.patterns = [re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in terms]
        self.patterns += [re.compile(p, re.IGNORECASE) for p in patterns]

    async def check(self, text: str, *, context: JSONObject) -> GuardrailDecision:
        hits = [p.pattern for p in self.patterns if p.search(text)]
        if not hits:
            return GuardrailDecision.allow(self.name)
        reason = f"prohibited content matched {len(hits)} rule(s)"
        if self.action == "redact":
            cleaned = text
            for pattern in self.patterns:
                cleaned = pattern.sub("[REMOVED]", cleaned)
            return GuardrailDecision.redact(self.name, cleaned, reason, rules=hits)
        if self.action == "block":
            return GuardrailDecision.block(self.name, reason, rules=hits)
        return GuardrailDecision.flag(self.name, reason, rules=hits)


class SchemaGuardrail:
    """Output must be JSON valid against a schema."""

    def __init__(self, schema: JSONObject, *, stage: Stage = "output") -> None:
        self.name = "schema"
        self.stage = stage
        self.schema = schema

    async def check(self, text: str, *, context: JSONObject) -> GuardrailDecision:
        try:
            data, _ = extract_json(text)
        except ValueError:
            return GuardrailDecision.block(self.name, "output is not JSON")
        errors = validate_json(self.schema, data)
        if errors:
            return GuardrailDecision.block(self.name, "output violates schema", errors=errors)
        return GuardrailDecision.allow(self.name)


class LengthGuardrail:
    def __init__(
        self, max_chars: int, *, stage: Stage = "both", action: Literal["block", "flag"] = "block"
    ) -> None:
        self.name = "length"
        self.stage = stage
        self.max_chars = max_chars
        self.action: Action = action

    async def check(self, text: str, *, context: JSONObject) -> GuardrailDecision:
        if len(text) <= self.max_chars:
            return GuardrailDecision.allow(self.name)
        reason = f"text length {len(text)} exceeds {self.max_chars}"
        if self.action == "block":
            return GuardrailDecision.block(self.name, reason, length=len(text))
        return GuardrailDecision.flag(self.name, reason, length=len(text))


class CallableGuardrail:
    """Wrap ``fn(text, context) -> bool | str | GuardrailDecision`` (sync or async)."""

    def __init__(
        self,
        fn: Callable[[str, JSONObject], Any | Awaitable[Any]],
        *,
        name: str | None = None,
        stage: Stage = "both",
        action: Literal["block", "flag"] = "block",
    ) -> None:
        self.fn = fn
        self.name: str = name or str(getattr(fn, "__name__", "callable"))
        self.stage = stage
        self.action: Action = action

    async def check(self, text: str, *, context: JSONObject) -> GuardrailDecision:
        import inspect

        result = self.fn(text, context)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, GuardrailDecision):
            return result
        if result is True or result is None:
            return GuardrailDecision.allow(self.name)
        reason = result if isinstance(result, str) else "check failed"
        if self.action == "block":
            return GuardrailDecision.block(self.name, reason)
        return GuardrailDecision.flag(self.name, reason)


class ModelGuardrail:
    """Ask a model whether text satisfies a policy (safety, factuality, style)."""

    def __init__(
        self,
        model: Model,
        policy: str,
        *,
        name: str = "model_policy",
        stage: Stage = "output",
        action: Literal["block", "flag"] = "block",
    ) -> None:
        self.model = model
        self.policy = policy
        self.name = name
        self.stage = stage
        self.action: Action = action

    async def check(self, text: str, *, context: JSONObject) -> GuardrailDecision:
        response = await self.model.agenerate(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a policy checker. Decide whether the text complies with the "
                        'policy. Reply with JSON: {"passed": true|false, "reason": "..."}.\n\n'
                        f"Policy: {self.policy}"
                    ),
                },
                {"role": "user", "content": text},
            ]
        )
        try:
            verdict, _ = extract_json(response.text)
        except ValueError:
            return GuardrailDecision.flag(self.name, "policy checker returned no verdict")
        passed = False
        reason = ""
        if isinstance(verdict, dict):
            passed = bool(verdict.get("passed"))
            reason = str(verdict.get("reason") or "")
        if passed:
            return GuardrailDecision.allow(self.name, reason)
        if self.action == "block":
            return GuardrailDecision.block(self.name, reason or "policy violation")
        return GuardrailDecision.flag(self.name, reason or "policy violation")
