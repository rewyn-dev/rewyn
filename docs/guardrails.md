# Guardrails

## Concept

A guardrail is a policy check on the way in or the way out, and the reason it
is a first-class primitive is that its decisions need to be auditable. "The
agent refused" and "the agent was blocked" look the same from the outside;
only one of them is a policy working.

Every check records a decision: `GUARDRAIL_PASSED` or `GUARDRAIL_TRIGGERED`
with the rule, the action and the reason. Actions are `allow`, `flag`,
`redact` or `block`.

Guardrails are not a substitute for permissions. A guardrail inspects text; a
permission policy controls what a tool may do. Use both.

## Minimal example

```python
from rewyn import Agent
from rewyn.guardrails import PIIGuardrail

agent = Agent(model="openai:gpt-5", guardrails=[PIIGuardrail(stage="output")])
result = agent.run("Summarise this support ticket")
```

## Production example

```python
from rewyn.models import Anthropic
from rewyn.guardrails import (
    CallableGuardrail,
    LengthGuardrail,
    ModelGuardrail,
    PIIGuardrail,
    ProhibitedContentGuardrail,
    PromptInjectionGuardrail,
    SchemaGuardrail,
)

agent = Agent(
    model="anthropic:claude-opus-5",
    guardrails=[
        PromptInjectionGuardrail(stage="input"),
        PIIGuardrail(stage="output", action="redact"),
        ProhibitedContentGuardrail(["guaranteed returns"], stage="output"),
        LengthGuardrail(max_chars=8000, stage="output"),
        SchemaGuardrail(Reply.model_json_schema(), stage="output"),
        ModelGuardrail(Anthropic("claude-haiku-4-5"), "the reply stays on-brand and calm"),
        CallableGuardrail(no_competitor_names, name="competitors", stage="output"),
    ],
)
```

Writing your own is a function:

```python
from rewyn.guardrails import GuardrailDecision


class AccountScope:
    name = "account_scope"
    stage = "output"

    async def check(self, text: str, *, context) -> GuardrailDecision:
        leaked = [a for a in ALL_ACCOUNTS if a in text and a != context.get("account")]
        if leaked:
            return GuardrailDecision.block(self.name, f"mentions {leaked[0]}")
        return GuardrailDecision.allow(self.name)
```

## API reference

`rewyn/guardrails/policy.py` for `Guardrail`, `GuardrailPolicy`,
`GuardrailDecision` and `GuardrailOutcome`.
`rewyn/guardrails/validators.py` for the built-ins.
`rewyn/guardrails/input.py` and `output.py` for the stage helpers.

## Failure modes

**A blocked output looks like a normal answer.** When an agent's output is
blocked, `stop_reason` is `GUARDRAIL` and the output is a message saying so.
Check `stop_reason`, not just the text.

**A redacting guardrail changes the answer silently.** That is the point,
but record it: the decision carries the original reason and the redacted
text.

**Injection heuristics miss.** `PromptInjectionGuardrail` is pattern-based.
It catches the common shapes, not a determined attacker. Combine it with
trust levels and permissions. See [Security](security.md).

**`ModelGuardrail` costs and fails.** It is a model call: it adds latency,
adds cost, and can itself be wrong. Use it for judgement calls that a
pattern cannot make, not for things a regex handles.

**A guardrail raises.** `GuardrailPolicy(on_block="raise")` raises
`GuardrailViolationError`; an agent uses `on_block="return"` so the run
completes with the block recorded. Pick deliberately.
