# Context engineering

## Concept

Prompt engineering is writing the instruction. Context engineering is
deciding what else goes in the window: which documents, which memories,
which tool descriptions, in what order, and what to drop when it does not
all fit. It is the part that actually determines the answer, and the part
that is usually done by string concatenation and hope.

`Context` makes it a decision you can inspect. Every item carries where it
came from, how far it is trusted, how much authority it has and how
sensitive it is. Assembly produces a `BudgetDecision` recording what was
included, what was excluded and why. That decision is an event, so when the
answer changes you can see whether the context did.

## Minimal example

```python
from rewyn.context import Context, text_item

context = Context(budget=4000)
context.add(text_item("Acme has paid every invoice on time since 2021.", source="crm"))

assembled = context.assemble_sync("Should we raise Acme's limit?")
print(assembled.text())
print(assembled.decision.render())
```

## Production example

Several sources, a trust boundary, freshness and compression:

```python
from datetime import timedelta

from rewyn.context import Context, FreshnessPolicy, SummaryCompressor, TrustLevel, text_item
from rewyn.models import Anthropic

context = Context(
    [rag.as_context_source(), memory.as_context_source()],
    budget=8000,
    freshness=FreshnessPolicy(timedelta(days=90)),
    compressor=SummaryCompressor(Anthropic("claude-haiku-4-5"), max_tokens=400),
    min_trust=TrustLevel.INTERNAL,
)

context.add(
    text_item(
        inbound_email,
        source="inbound-email",
        trust_level=TrustLevel.UNTRUSTED,
    )
)

agent = Agent(model=..., context=context)
```

### Trust is the important part

`TrustLevel` runs `SYSTEM`, `TRUSTED`, `INTERNAL`, `EXTERNAL`, `UNTRUSTED`.
Untrusted content is rendered inside an explicit boundary with a notice, and
it cannot outrank policy in the ordering. A customer email that says "ignore
your policy" is shown to the model as quoted data, clearly marked, below the
policy that governs it.

`min_trust` drops anything below a level entirely. Use it when a source
should not be in the window at all.

### Provenance

```python
from rewyn.context import provenance_report

for record in provenance_report(assembled.items):
    print(record["source"], record["version"], record["verified"])
```

`verified` re-hashes the content against what was recorded at retrieval. It
answers "is this the document we actually read?" six weeks later.

## API reference

`rewyn/context/manager.py` for `Context` and `AssembledContext`.
`rewyn/context/source.py` for `ContextItem`, `ContextKind`, `TrustLevel`,
`Sensitivity` and `text_item`.
`rewyn/context/budget.py` for `BudgetDecision` and `allocate`.
`rewyn/context/ranking.py`, `freshness.py`, `compression.py`,
`provenance.py`.

## Failure modes

**Everything was dropped.** The budget is too small for the required items,
or `min_trust` is excluding your sources. `assembled.decision.render()` lists
every exclusion with its reason.

**Token counts look wrong.** The default counter is a fast estimate, not the
provider's tokenizer. Pass `token_counter=` for exact accounting.

**Untrusted content changed behaviour anyway.** The trust boundary makes
injection visible and lowers its priority; it is not a guarantee. Pair it
with a `PromptInjectionGuardrail` and keep high-risk tools behind approval.
See [Security](security.md).

**The same fact appears three times.** Assembly de-duplicates by content
hash, but three sources paraphrasing one fact are three distinct items. That
is a retrieval problem, not a context one.
