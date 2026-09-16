# Models

## Concept

Every provider has a different message format, a different tool-calling
shape, a different streaming protocol and a different idea of what a token
costs. A model abstraction that only smooths over the request shape leaves
you re-learning the rest.

Rewyn gives you one `Model` interface and keeps provider code in exactly
one place: `rewyn/models/<provider>.py`. Nothing else in the SDK imports a
provider SDK or assumes a provider's message format. Capabilities that are
genuinely provider-specific are not hidden or emulated; they go through an
explicit `provider_options` escape hatch, so you can tell from the code when
you have left portable ground.

Every call emits `MODEL_CALLED` and `MODEL_RESPONSE` with the full request,
the response, usage and cost. That is what makes a run replayable later.

## Minimal example

```python
from rewyn import models

model = models.from_string("anthropic:claude-opus-5")
response = model.generate("Summarise the EV market in one sentence.")

print(response.text)
print(response.usage.total_tokens, response.cost.total)
```

Async is the native form; the sync methods are thin facades.

```python
response = await model.agenerate("Summarise the EV market in one sentence.")

async for event in model.astream("Write a haiku about latency"):
    if event.type == "text_delta":
        print(event.text, end="")
```

## Production example

Structured output, a tool definition, provider-specific options and an
explicit budget:

```python
from pydantic import BaseModel

from rewyn.models import Anthropic, ToolSpec


class Verdict(BaseModel):
    approved: bool
    limit: int
    reason: str


model = Anthropic("claude-opus-5", defaults={"max_tokens": 2048})

response = await model.agenerate(
    [
        {"role": "system", "content": "You are a credit analyst."},
        {"role": "user", "content": "Should we raise Acme's limit?"},
    ],
    tools=[ToolSpec(name="bureau_score", parameters={"type": "object"})],
    output_schema=Verdict,
    temperature=0.0,
    provider_options={"thinking": {"type": "enabled", "budget_tokens": 1024}},
)

verdict: Verdict = response.structured
```

`output_schema` validates the reply and emits `OUTPUT_VALIDATED` with the
errors when it fails. `strict_output=False` returns the invalid response
instead of raising, which is what an agent loop wants so it can retry.

### Cost

Cost is computed from a price table, not guessed. Register prices for models
the table does not know, including your own fine-tunes:

```python
from rewyn.models import Price, register_price

register_price("openai", "ft:my-model", Price(input_per_million=2.0, output_per_million=8.0))
```

Prices match by longest prefix, so `"gpt-5"` covers `"gpt-5-2026-01-01"`.

## API reference

`rewyn/models/base.py` for `Model`, `Message`, `ModelRequest`,
`ModelResponse`, `Usage`, `Cost`, `ToolSpec` and `StreamEvent`.
`rewyn/models/registry.py` for `from_string` and `register_provider`.
`rewyn/models/pricing.py` for the cost table.

Adapters: `openai.py`, `anthropic.py`, `google.py`, `openai_compatible.py`
(anything speaking the OpenAI API, including local servers), `local.py`.

## Failure modes

**`MissingDependencyError`.** The provider extra is not installed. The
message tells you which one: `pip install "rewyn[anthropic]"`.

**`StructuredOutputError`.** The model's reply did not satisfy
`output_schema`. The exception carries both the validation errors and the
response, so you can inspect what it actually said. Rewyn tolerates code
fences and surrounding prose before giving up.

**Cost is zero.** No price is registered for that provider and model. Use
`register_price`. Rewyn reports `cost.source == "unknown"` rather than
inventing a number.

**A provider feature is missing.** It is not emulated. Pass it through
`provider_options` and it reaches the SDK verbatim.

**Streaming ends with no response.** A `_stream` implementation must finish
with a `response` event. Custom adapters that forget raise `ModelError`.
