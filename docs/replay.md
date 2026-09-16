# Replay

## Concept

Replay is the point of recording everything. Two questions matter, and they
need different mechanisms.

**What happened?** Deterministic replay reproduces a run exactly, using the
recorded responses. No provider is called, so there is no cost, no latency
and no chance of a different answer. This is what you do when a production
run went wrong and you need to see it again.

**What would happen if I changed this?** Live replay re-executes selected
components against the recording. A new model, the same prompts. A fixed
tool, the same conversation. Everything you did not change still comes from
the recording, so the change is the only variable.

## Minimal example

```python
from rewyn.replay.replay import replay

result = replay(run_id)
print(result.identical, result.output)
```

## The three modes

`replay()` picks one based on what you give it.

### Reconstruct

Nothing overridden, no target. The recording is re-emitted into a fresh run
and the output is the recorded output.

```python
result = replay(run_id)
assert result.identical and result.faithful
```

### Prompt replay

A different model, no target. Every recorded model request is re-issued
against the new model and the answers compared. No application code needed,
because the recorded transcript is the prompt.

```python
result = replay(run_id, model="anthropic:claude-opus-5", context="original", tools="recorded")

for prompt in result.changed_prompts:
    print(prompt.index, prompt.original_text[:60], "->", prompt.replay_text[:60])
print(result.cost_delta)
```

This is a prompt-level experiment, not a re-run of the agent: a divergence
at call three does not change the prompt of call four.

Two things can be varied without touching the transcript, because they are
the two developers vary most:

```python
result = replay(run_id, model="anthropic:claude-opus-5", temperature=0.9, system="Be terse.")
```

`temperature` overrides the recorded generation options and `system` replaces
the system message of every recorded request. Everything else -- the
conversation, the tool definitions, the output schema -- stays exactly as it
was recorded.

### Re-execute

A target really runs, with recorded components substituted.

```python
agent = Agent(model=..., tools=[...])  # your current code

result = replay(run_id, target=agent, model="recorded", tools="recorded")
assert result.identical  # same output
assert result.faithful  # and the same call sequence
```

`identical` means the output matched. `faithful` means the replay also
followed the recording call for call. A replay can be the first without the
second, and `result.mismatches` says which request diverged.

Set `tools="live"` to re-execute the real tools against recorded model
responses, which is how you test a tool fix against a real conversation.

## Production example

```python
from rewyn.replay.recorder import RecordedRun
from rewyn.replay.replay import areplay

recorded = RecordedRun.load(run_id)
print(len(recorded.model_calls), [c.name for c in recorded.tool_calls])

result = await areplay(
    recorded,
    target=agent,
    model=candidate_model,  # the new model
    tools="recorded",  # the same tool results
    context="original",  # the recorded prompt, not a fresh assembly
    strict=False,  # collect divergences rather than raising
)
```

```bash
rewyn replay latest
rewyn replay run_01J... --model anthropic:claude-opus-5
```

## API reference

`rewyn/replay/replay.py` for `replay`, `areplay`, `ReplayResult` and
`ReplayMode`.
`rewyn/replay/recorder.py` for `RecordedRun`, `RecordedModelCall` and
`RecordedToolCall`.
`rewyn/replay/deterministic.py` for `ReplayHooks`, `ComponentMode` and
`Mismatch`.
`rewyn/replay/live.py` for `replay_prompts`.
`rewyn/replay/export.py` for portable run bundles.

## Failure modes

**`identical` is true but `faithful` is false.** The output matched while
some request did not line up with the recording, so it was answered by
position. Usually a changed model name, a changed prompt or a changed tool
schema. `result.mismatches` names it.

**`ReplayError: live replay needs target=`.** Live components need something
to execute. Pass an agent, a graph or a callable.

**The recording ran out.** The replayed code made more model calls than were
recorded. Lenient mode falls through to the real provider; `strict=True`
raises `ReplayExhaustedError` instead.

**Replay of a run with live side effects.** `tools="recorded"` prevents tool
functions from running. `tools="live"` really calls them, including the ones
that charge money.

**A run cannot be found.** Replay reads local storage. Pull it first if it
only exists in the cloud: `rewyn sync --pull <run_id>`.
