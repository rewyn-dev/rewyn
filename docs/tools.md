# Tools

## Concept

A tool is not just a function the model can call. It is an interface you have
to version, a permission boundary, and a risk you have to be able to audit
afterwards. "The agent issued a refund" is a sentence someone will say to
you, and you will need to answer which tool, which version, what arguments
and who allowed it.

`@tool` derives the JSON schema from the signature, validates arguments
before the function ever runs, and carries the metadata that makes the
question answerable: version, owner, risk level and required permissions.

## Minimal example

```python
from rewyn import tool


@tool
def get_customer(customer_id: str) -> dict:
    """Fetch a customer record."""
    return {"id": customer_id, "plan": "enterprise"}


get_customer.parameters  # JSON schema derived from the signature
get_customer.fingerprint()  # content hash, recorded with every call
```

The first line of the docstring becomes the description the model sees.
Write it for the model, not for your colleagues.

## Production example

```python
from rewyn import tool
from rewyn.tools import CompositePolicy, MaxRiskLevel, RequirePermissions, RiskLevel


@tool(
    version="3",
    owner="payments-team",
    risk_level="high",
    permissions=["billing:write"],
    timeout=10.0,
)
def issue_refund(customer_id: str, amount_cents: int, reason: str) -> str:
    """Issue a refund to a customer. Amounts are in cents."""
    return billing.refund(customer_id, amount_cents, reason)


policy = CompositePolicy(
    [
        MaxRiskLevel(approve_above=RiskLevel.MEDIUM),  # high risk asks a human
        RequirePermissions({"billing:write"}),  # this caller holds this grant
    ]
)

agent = Agent(model=..., tools=[issue_refund], permission_policy=policy)
```

A policy can allow, deny, or require approval, and the difference matters:

```python
MaxRiskLevel(RiskLevel.MEDIUM)  # high risk is DENIED outright
MaxRiskLevel(approve_above=RiskLevel.MEDIUM)  # high risk ASKS a human
MaxRiskLevel(RiskLevel.HIGH, approve_above=RiskLevel.MEDIUM)  # ask, but never critical
```

The first argument is the ceiling above which a call is refused. Pass
`approve_above` when you want a person in the loop rather than a wall.

When approval is required the executor asks the configured handler before
running anything, and the decision is recorded as `HUMAN_APPROVED` or
`HUMAN_REJECTED`.

### Async, concurrency and timeouts

Async tools are awaited; sync tools run in a thread so they never block the
loop. Tool calls in one model turn execute concurrently, bounded by
`ToolExecutor(max_concurrency=...)`. `timeout` is per call.

### Reporting progress

A tool written as an async generator streams: each yield is an interim
update and the last one is the result.

```python
@tool
async def crawl(pages: int) -> str:
    """Crawl pages, reporting progress."""
    for index in range(pages):
        yield f"crawled page {index + 1}"
    yield f"done: {pages} pages"
```

Progress is published to the run like a model token delta rather than
emitted as an event, so a tool reporting a hundred times reaches a live view
without putting a hundred rows in the log. `TOOL_RETURNED` carries the
count. Read them from `Agent.astream`; see [Loops](loops.md).

### Cost

```python
@tool(cost_per_call=0.0025)
def bureau_score(account_id: str) -> int:
    """Fetch a credit bureau score."""
    ...
```

The cost lands in `run.manifest.cost.tool`. Prices can also be registered
centrally with `register_unit_price`, which is how you price a whole MCP
server by prefix. See [Production](production.md).

## API reference

`rewyn/tools/tool.py` for `tool`, `Tool`, `make_tool` and `as_tool`.
`rewyn/tools/permissions.py` for `RiskLevel`, `AllowAll`, `DenyAll`,
`AllowList`, `MaxRiskLevel`, `RequirePermissions` and `CompositePolicy`.
`rewyn/tools/execution.py` for `ToolExecutor`.

## Failure modes

**`ToolArgumentError`.** The model sent arguments that do not fit the
schema. The error lists each field. This is returned to the model by default
so it can correct itself, which is usually what you want.

**The model never calls the tool.** Almost always the description. It is the
only thing the model sees; a docstring written for a human reader often does
not say when to use the function.

**The schema is wrong.** It comes from your type hints. An unannotated
parameter becomes `Any`, which tells the model nothing. Annotate everything.

**A denied call looks like a failure.** Denials emit `TOOL_DENIED` and then
return an error result to the model. Search for `TOOL_DENIED` rather than
inferring from the result text.

**A blocking tool stalls the loop.** Sync tools run in a thread, but a sync
tool that never returns still holds that thread. Set `timeout`.
