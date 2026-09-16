# Subagents

## Concept

A subagent is a scoped delegation: the parent hands off one task, the child
runs its own loop with its own tools and budget, and returns a result. The
parent's context stays clean, because the child's twenty tool calls collapse
into one answer.

The alternative, one agent with thirty tools, degrades in a specific way:
tool selection gets worse as the list grows, and the transcript fills with
intermediate work that the final answer does not need.

Subagent runs are nested runs. `SUBAGENT_STARTED` and `SUBAGENT_FINISHED`
bracket them, and the child's cost rolls up into the parent's.

## Minimal example

```python
from rewyn import Agent

researcher = Agent(model="openai:gpt-5-mini", name="researcher", tools=[search])
lead = Agent(model="openai:gpt-5", name="lead", subagents=[researcher])

result = lead.run("What is the EV market share in Europe?")
```

The parent gets a `researcher(task)` tool automatically. It decides when to
delegate.

## Production example

```python
researcher = Agent(
    model="anthropic:claude-haiku-4-5",  # cheap model for gathering
    name="researcher",
    instructions="Gather facts. Report what you found, not what you think.",
    tools=[search, fetch_document],
    max_iterations=6,
    max_cost=0.10,  # the child has its own budget
)

analyst = Agent(
    model="anthropic:claude-opus-5",  # expensive model for judgement
    name="analyst",
    instructions="You are a careful credit analyst.",
    subagents=[researcher],
    skills=["./skills/credit-policy"],
    max_cost=1.00,
)
```

Splitting models by role is the usual reason to reach for a subagent: the
gathering is mechanical and the judgement is not.

## API reference

`rewyn/agents/subagent.py` for `subagent_tool` and the run scoping.
`rewyn/agents/agent.py` for the `subagents=` parameter.

## Failure modes

**The parent never delegates.** The subagent tool's description comes from
the child's `instructions`. Vague instructions produce a vague tool
description and the parent does the work itself.

**Cost doubles.** Each delegation is a full nested loop. `max_cost` on the
parent covers the total; set one on the child as well.

**The child's findings are lost.** The parent sees only the child's final
output. If intermediate detail matters, have the child return structured
output rather than prose.

**Infinite delegation.** An agent listed as its own subagent recurses. The
budgets stop it, but the run is wasted; check your wiring.

**The child cannot see the parent's context.** That is the point. Pass what
it needs in the task description.
