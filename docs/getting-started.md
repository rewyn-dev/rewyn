# Getting started

## Install

```bash
pip install rewyn                # core, provider agnostic
pip install "rewyn[anthropic]"   # add a provider
pip install "rewyn[all]"         # everything
```

Rewyn is local-first. There is no account, no Rewyn API key and no
network call to us. State lives in `.rewyn/` in your project.

## Your first agent

```python
from rewyn import Agent, tool


@tool
def ev_market_share(region: str) -> dict[str, float]:
    """Return electric vehicle share of new car sales for a region."""
    return {"share": {"europe": 24.5, "china": 41.0}.get(region.lower(), 0.0)}


agent = Agent(model="anthropic:claude-opus-5", tools=[ev_market_share])
result = agent.run("Compare EV adoption in Europe and China")

print(result.output)
print(result.usage.total_tokens, result.cost)
```

That run is already recorded. Nothing was configured to make that happen.

```bash
rewyn runs                 # every run, newest first
rewyn inspect latest       # manifest, dependencies and the event timeline
```

## Without a provider key

`FakeModel` scripts responses, so tests and demos run offline and
deterministically. Everything else behaves identically.

```python
from rewyn import Agent
from rewyn.testing import FakeModel

model = FakeModel(["Europe is at 24.5%, China at 41.0%."])
result = Agent(model=model, name="ev").run("Compare EV adoption")
assert result.output.startswith("Europe")
```

## The idea

Every primitive emits a structured event into the active run: model calls,
tool calls, retrievals, memory reads, guardrail decisions, approvals. That
event log is what makes the interesting operations possible.

```python
from rewyn.replay.replay import replay
from rewyn.replay.diff import diff

replay(result.run_id)  # reproduce it exactly, no provider call
diff(first_run_id, second_run_id)  # what changed between two runs
```

Because the log exists, a production run can become a regression test:

```python
from rewyn.evaluation.dataset import Dataset
from rewyn.evaluation.metrics import exact_match
from rewyn.evaluation.regression import run_regression

dataset = Dataset(name="critical")
dataset.add_run(result.run_id)  # this run is now a golden example
dataset.save()

report = run_regression(dataset, agent, evaluators=[exact_match()])
print(report.render())
```

## Where things live

```
.rewyn/
├── runs/<run_id>/manifest.json    what the run was and what it cost
├── runs/<run_id>/events.jsonl     everything that happened, in order
├── datasets/                      evaluation datasets
├── evaluations/                   regression reports
├── checkpoints/                   resumable state
└── credentials.json               cloud credentials, if you use the cloud
```

Set `REWYN_HOME` to move it. Set `REWYN_RECORDING=0` to turn recording
off.

## Failure modes

**Nothing was recorded.** Recording is on unless `REWYN_RECORDING` is
falsey. The recorder is asynchronous, so in a short-lived script call
`rewyn.runtime.default_recorder().flush()` before the process exits.

**`ConfigurationError: model must be given as 'provider:model'`.** Model
strings need the provider prefix: `"anthropic:claude-opus-5"`, not
`"claude-opus-5"`.

**The agent stopped early.** Check `result.stop_reason`. Budgets
(`max_iterations`, `max_tokens`, `max_cost`, `max_time`) stop a loop and say
so rather than running away.

## Next

[Agents](agents.md) for the loop, [Context engineering](context-engineering.md)
for what goes in the prompt, [Replay](replay.md) for what to do with a run
once you have it.
