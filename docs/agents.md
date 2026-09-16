# Agents

## Concept

An agent is a loop: call the model, execute whatever tools it asked for, feed
the results back, repeat until it stops asking. That loop is where a
prototype becomes an outage, because nothing about it is bounded by default.
An agent that misreads a tool result can call it a thousand times.

`Agent` is that loop with budgets, an explicit stop reason, and an event for
every step. It is also the assembly point: context, memory, skills, MCP
servers, guardrails, subagents and approvals all attach here.

## Minimal example

```python
from rewyn import Agent, tool


@tool
def get_customer(customer_id: str) -> dict:
    """Fetch a customer record."""
    return {"id": customer_id, "plan": "enterprise"}


agent = Agent(model="openai:gpt-5", tools=[get_customer])
result = agent.run("What plan is customer 4417 on?")

print(result.output)
print(result.stop_reason, result.iterations, result.tool_calls)
```

## Production example

```python
from rewyn import Agent
from rewyn.context import Context
from rewyn.guardrails import PIIGuardrail
from rewyn.human import ConsoleHandler
from rewyn.memory import Memory
from rewyn.tools import MaxRiskLevel, RiskLevel

agent = Agent(
    model="anthropic:claude-opus-5",
    name="support",
    version="7",
    instructions="You are a support agent. Never promise a refund.",
    tools=[get_customer, issue_refund],
    context=Context(budget=8000),
    memory=Memory(),
    skills=["./skills/refund-policy"],
    mcp=["https://crm.internal/mcp"],
    guardrails=[PIIGuardrail(stage="output")],
    permission_policy=MaxRiskLevel(approve_above=RiskLevel.MEDIUM),
    approval_handler=ConsoleHandler(),
    max_iterations=8,
    max_cost=0.50,
    max_time=60.0,
    output_schema=SupportReply,
)

async with agent:  # closes MCP connections it opened
    result = await agent.arun("Customer 4417 wants a refund")
```

`version` matters. It goes into the run manifest and the behavior manifest,
so a regression six weeks later can tell you which version of this agent
produced which answer.

### Streaming

```python
async for item in agent.astream("Research the EV market"):
    ...  # events and token deltas as they happen; the last item is the RunResult
```

### Budgets and stopping

`max_iterations`, `max_tokens`, `max_cost` and `max_time` each stop the loop
and set `stop_reason`. So does a custom condition:

```python
agent = Agent(model=..., stop_when=lambda ctx: "FINAL" in ctx.output)
```

`StopReason` distinguishes `COMPLETED`, `MAX_ITERATIONS`, `MAX_COST`,
`GUARDRAIL`, `HANDOFF`, `EVALUATOR`, `CUSTOM` and `ERROR`. Log it. A
silently truncated answer and a finished one look identical otherwise.

## API reference

`rewyn/agents/agent.py` for `Agent` and `RunResult`.
`rewyn/agents/loop.py` for `Loop`, `LoopContext` and `StopReason`.
`rewyn/agents/lifecycle.py` for hooks.

## Failure modes

**The loop hits `max_iterations`.** Usually the model keeps calling a tool
that returns something it cannot use. Inspect the run: `rewyn inspect
latest` shows every tool call and result in order.

**A tool error is swallowed.** By default a failing tool returns an error
string to the model so it can recover, and the run still succeeds. That is
usually right, and it is also how a broken integration hides. Use the
`no_errors()` evaluator, or `raise_on_tool_error=True` when you would rather
fail loudly.

**Costs are higher than expected.** Tool results are appended to the prompt
every iteration, so input tokens grow quadratically with loop length. Set
`max_cost` and check `result.usage`.

**MCP connections leak.** Use `async with agent:` or call `agent.close()`.
The agent only closes connections it opened itself.

**Structured output is `None`.** The model answered but the reply did not
satisfy `output_schema`. The agent makes one repair attempt; if that fails,
`result.structured` stays `None` and `OUTPUT_VALIDATED` records why.
