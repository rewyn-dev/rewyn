# Handoffs

## Concept

A handoff transfers the conversation. Unlike a subagent, the first agent
stops: triage decides this is a billing question, billing takes over, and
the user talks to billing from then on.

The distinction matters operationally. A subagent returns to its parent, so
the parent owns the final answer. A handoff does not, so the receiving agent
does. `HANDOFF` records the transfer with the reason and the summary the
sender wrote, which is what you read when a conversation ended up somewhere
strange.

## Minimal example

```python
from rewyn import Agent

billing = Agent(model="openai:gpt-5", name="billing", tools=[issue_refund])
triage = Agent(model="openai:gpt-5-mini", name="triage", handoffs=[billing])

result = triage.run("I want a refund for last month")
print(result.stop_reason)  # StopReason.HANDOFF
print(result.output)  # billing's answer
```

Each target adds a `handoff_to_<name>(reason, summary)` tool to the sender.

## Production example

```python
triage = Agent(
    model="anthropic:claude-haiku-4-5",
    name="triage",
    instructions=(
        "Route the customer. Hand off to billing for payments and refunds, "
        "to technical for errors and outages. Do not attempt to answer yourself."
    ),
    handoffs=[billing, technical],
)

result = await triage.arun(message)
if result.stop_reason is StopReason.HANDOFF:
    log.info("handed off", extra={"to": result.state.get("handoff")})
```

The transcript travels with the handoff, so the receiver has the
conversation, plus the sender's summary of why it transferred.

## API reference

`rewyn/agents/handoff.py` for `handoff`, `handoff_tool` and the handoff
record.
`rewyn/agents/agent.py` for the `handoffs=` parameter.

## Failure modes

**Ping-pong.** Two agents listing each other hand back and forth. Give each
a clear scope, and bound the interaction with `max_iterations` on both.

**The sender answers instead of routing.** Instructions again: say
explicitly that it should not attempt an answer.

**Context is lost.** The transcript transfers, but the sender's internal
state does not. Put anything the receiver needs into the summary.

**Handoff looks like a failure.** `stop_reason` is `HANDOFF`, not
`COMPLETED`. Treat it as success in your own reporting, or you will see a
phantom failure rate.
