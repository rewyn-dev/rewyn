"""Guardrail protocol and policy (spec §25).

Every guardrail decision is recorded: ``GUARDRAIL_TRIGGERED`` when a check
fails (block, redact or flag) and ``GUARDRAIL_PASSED`` otherwise.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import EventType
from rewyn.core.run import aensure_run
from rewyn.core.span import SpanKind
from rewyn.core.types import JSONObject, RewynError

Stage = Literal["input", "output", "both"]
Action = Literal["allow", "block", "redact", "flag"]


class GuardrailDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    guardrail: str
    passed: bool
    action: Action = "allow"
    reason: str = ""
    text: str | None = None
    details: JSONObject = Field(default_factory=dict)

    @classmethod
    def allow(cls, guardrail: str, reason: str = "") -> GuardrailDecision:
        return cls(guardrail=guardrail, passed=True, action="allow", reason=reason)

    @classmethod
    def block(cls, guardrail: str, reason: str, **details: Any) -> GuardrailDecision:
        return cls(
            guardrail=guardrail, passed=False, action="block", reason=reason, details=details
        )

    @classmethod
    def redact(cls, guardrail: str, text: str, reason: str, **details: Any) -> GuardrailDecision:
        return cls(
            guardrail=guardrail,
            passed=False,
            action="redact",
            reason=reason,
            text=text,
            details=details,
        )

    @classmethod
    def flag(cls, guardrail: str, reason: str, **details: Any) -> GuardrailDecision:
        return cls(guardrail=guardrail, passed=False, action="flag", reason=reason, details=details)


@runtime_checkable
class Guardrail(Protocol):
    name: str
    stage: Stage

    async def check(self, text: str, *, context: JSONObject) -> GuardrailDecision: ...


class GuardrailViolationError(RewynError):
    def __init__(self, decision: GuardrailDecision, stage: str) -> None:
        super().__init__(f"{stage} blocked by guardrail {decision.guardrail!r}: {decision.reason}")
        self.decision = decision
        self.stage = stage


class GuardrailOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    text: str
    blocked: bool = False
    decisions: list[GuardrailDecision] = Field(default_factory=list)

    @property
    def triggered(self) -> list[GuardrailDecision]:
        return [d for d in self.decisions if not d.passed]


class GuardrailPolicy:
    """Run a set of guardrails over text for a stage and record the decisions."""

    def __init__(
        self,
        guardrails: Iterable[Guardrail] = (),
        *,
        on_block: Literal["raise", "return"] = "raise",
    ) -> None:
        self.guardrails = list(guardrails)
        self.on_block = on_block

    def for_stage(self, stage: Literal["input", "output"]) -> list[Guardrail]:
        return [g for g in self.guardrails if g.stage in (stage, "both")]

    async def run(
        self, stage: Literal["input", "output"], text: str, *, context: JSONObject | None = None
    ) -> GuardrailOutcome:
        outcome = GuardrailOutcome(stage=stage, text=text)
        guardrails = self.for_stage(stage)
        if not guardrails:
            return outcome
        async with aensure_run("guardrails") as run:
            with run.span(f"guardrails:{stage}", SpanKind.GUARDRAIL):
                for guardrail in guardrails:
                    decision = await guardrail.check(outcome.text, context=context or {})
                    outcome.decisions.append(decision)
                    payload = {
                        "stage": stage,
                        "guardrail": decision.guardrail,
                        "action": decision.action,
                        "reason": decision.reason,
                        "details": decision.details,
                    }
                    if decision.passed:
                        run.emit(EventType.GUARDRAIL_PASSED, payload)
                        continue
                    run.emit(EventType.GUARDRAIL_TRIGGERED, payload)
                    if decision.action == "redact" and decision.text is not None:
                        outcome.text = decision.text
                    elif decision.action == "block":
                        outcome.blocked = True
                        if self.on_block == "raise":
                            raise GuardrailViolationError(decision, stage)
                        break
        return outcome

    async def check_input(self, text: str, **context: Any) -> GuardrailOutcome:
        return await self.run("input", text, context=context)

    async def check_output(self, text: str, **context: Any) -> GuardrailOutcome:
        return await self.run("output", text, context=context)
