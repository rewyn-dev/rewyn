# Loops

## Concept

"Agent" mostly means "a loop around a model". The strategy you pick for that
loop decides how the system behaves when the task is hard: whether it reacts
step by step, plans first, or checks its own work.

Rewyn ships three strategies and lets you add your own. All of them run
under the same budgets and emit the same `AGENT_LOOP_STARTED`,
`AGENT_LOOP_ITERATION` and `AGENT_LOOP_FINISHED` events, so you can compare
them on the same task with a diff.

| Strategy | Shape | Good for |
| --- | --- | --- |
| `react` | Think, act, observe, repeat | Most tasks; the default |
| `plan_execute` | Plan the whole thing, then execute the steps | Multi-step work with a clear decomposition |
| `reflection` | Answer, critique the answer, revise | Quality-sensitive output where a second pass pays |

## Minimal example

```python
from rewyn import Agent

agent = Agent(model="openai:gpt-5", tools=[search], loop="plan_execute")
result = agent.run("Research the EV market and summarise the top three trends")

print(result.stop_reason, result.iterations)
```

`PLAN_CREATED` is emitted when a plan strategy produces one, so the plan is
in the run log rather than lost inside a prompt.

## Production example

```python
from rewyn.agents.loop import Loop

agent = Agent(
    model="anthropic:claude-opus-5",
    tools=[search, summarise],
    loop=Loop(
        strategy="reflection",
        max_iterations=10,
        max_tokens=200_000,
        max_cost=1.00,
        max_time_seconds=120.0,
        strategy_options={"max_revisions": 2},
    ),
)
```

### Stopping on your own condition

```python
agent = Agent(model=..., stop_when=lambda ctx: "APPROVED" in ctx.output)
```

An evaluator can terminate the loop too, which is how you say "keep going
until the answer is good enough":

```python
from rewyn.evaluation.evaluator import evaluator


@evaluator(threshold=0.8)
def good_enough(subject) -> float:
    return score_answer(subject.output)


agent = Agent(model=..., stop_when=good_enough)
```

### Reading the outcome

`StopReason` is one of `completed`, `max_iterations`, `max_tokens`,
`max_cost`, `max_time`, `custom`, `evaluator`, `guardrail`, `handoff`,
`error` or `cancelled`. Log it on every run. A truncated answer and a
finished one are indistinguishable from the text alone.

## API reference

`rewyn/agents/loop.py` for `Loop`, `LoopContext`, `StopReason` and
`get_strategy`.
`rewyn/agents/planner.py` for the plan-execute strategy.

## Failure modes

**The loop never terminates.** It does; `max_iterations` defaults to a
finite number. What it does not do is finish the task. Check whether the
model is repeating a tool call with the same arguments, which usually means
the result is unusable to it.

**`plan_execute` is slower and costlier for simple tasks.** Planning is an
extra model call. Use `react` unless the decomposition genuinely helps.

**`reflection` agrees with itself.** Self-critique with the same model and
prompt often rubber-stamps. Give the critic different instructions, or use
an LLM judge with a separate model. See [Evaluation](evaluation.md).

**Cost climbs faster than iterations.** Every iteration resends the growing
transcript. Cost is roughly quadratic in loop length; budget on `max_cost`,
not `max_iterations`.
