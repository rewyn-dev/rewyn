# Production

## Concept

The recording layer is infrastructure inside someone else's application. It
has one hard requirement: it must never be the reason their system fails.
Everything below follows from that.

## Recording

Recording is asynchronous, batched, buffered locally and fails open. Events
go on a queue; a background thread writes them. If the writer throws, it
logs and the application keeps running. If the queue fills, events are
dropped and counted rather than blocking the caller.

```python
from rewyn.runtime import default_recorder

recorder = default_recorder()
recorder.flush()  # before a short-lived process exits
print(recorder.dropped, recorder.failures)
```

```bash
REWYN_RECORDING=0       # off entirely
REWYN_SAMPLE_RATE=0.1   # record a tenth of runs
REWYN_HOME=/var/lib/rewyn
REWYN_PROJECT=support-agent
```

### Sampling

Past a certain volume you cannot record everything. Sampling is **per run,
not per event**, because half a run's events cannot be replayed, diffed or
evaluated. The decision is a hash of the run id, so it is deterministic and
two processes agree without coordinating.

Two things are never dropped. Runs that fail are kept in full: their events
are buffered while the run is in flight and promoted the moment it fails,
rather than leaving an unreplayable failure event on its own. And a run you
mark explicitly survives any rate:

```python
from rewyn.core.run import start_run
from rewyn.runtime import RateSampler, always_record

with start_run("checkout", tags=always_record(["critical"])) as run:
    ...

recorder = Recorder(sampler=RateSampler(0.05))
print(recorder.sampled_out, recorder.dropped)
```

Long-running services should not accumulate runs on local disk forever.
Either sync to the [cloud](cloud.md) and prune, or point `REWYN_HOME` at a
volume you rotate.

## Cost

All five categories the spec lists are recorded, not just models:

```python
cost = run.manifest.cost
print(cost.model, cost.tool, cost.embedding, cost.retrieval, cost.sandbox)
print(cost.total)
```

Tokens are priced per million; everything else is priced per call, per
second or per unit:

```python
from rewyn.models import Price, UnitPrice, register_price, register_unit_price

register_price("openai", "ft:my-model", Price(2.0, 8.0))
register_unit_price("tool", "bureau_score", UnitPrice(per_call=0.0025))
register_unit_price("embedding", "text-embedding-3", UnitPrice(per_million_units=0.13))
register_unit_price("retrieval", "docs", UnitPrice(per_call=0.0001))
register_unit_price("sandbox", "container", UnitPrice(per_second=0.0002))
```

A tool can also declare its own price with `@tool(cost_per_call=0.01)`. That
is deliberately not part of the tool's fingerprint: a price change is not a
behaviour change.

Budgets stop a loop before the bill does: `max_cost` on the agent,
`max_cost_per_run` on a release gate. Cost grows faster than iteration count
because every iteration resends the transcript, so budget on money, not on
steps.

Register prices for models the table does not know, including fine-tunes.
An unknown price reports `cost.source == "unknown"` and zero, rather than
guessing.

## Latency and streaming

```python
async for item in agent.astream(question):
    ...  # events and token deltas as they happen
```

Instrumentation adds bounded overhead: an event is a dict put on a queue.
The expensive things are model calls and tool calls, both of which are
recorded with their own latency.

## Observability

Rewyn keeps its own event log as the source of truth and mirrors into
OpenTelemetry for teams who already have a collector.

```python
from rewyn.integrations.otel import OTelExporter

with start_run("support") as run:
    OTelExporter().attach(run)
    ...
```

Needs `pip install "rewyn[otel]"`. Like the recorder, it fails open.

## Reliability

```python
from rewyn.runtime import CancellationToken, cancellation_scope

token = CancellationToken()
with cancellation_scope(token):
    task = asyncio.create_task(agent.arun(question))
    token.cancel("user navigated away")
```

Graphs support retries and timeouts per node, and checkpoints for resume.
See [Graphs](graphs.md) and [State](state.md).

## Schema migrations

Recorded runs outlive the code that produced them. Every persisted object
declares a `schema_version`, and migrations registered against a schema are
applied on read, so a run captured last quarter still loads, replays and
diffs:

```python
from rewyn.core.migrations import migration


@migration("event", "1")
def add_severity(record: dict) -> dict:
    return {**record, "severity": "info"}
```

A record written by a *newer* build is passed through untouched rather than
mangled, because silently dropping fields you do not understand is worse
than not understanding them.

## Versioning

Everything versionable carries a `version` and a content `fingerprint`:
agents, tools, skills, graphs, context configs, MCP configs, datasets and
policies. Run manifests record all of them, which is what makes it possible
to reconstruct which configuration produced a historical run.

```python
from rewyn.core.manifest import BehaviorManifest, detect_drift

manifest = BehaviorManifest.from_runs("support-agent", run_ids, version="1.4.2")
manifest.save()

report = detect_drift(BehaviorManifest.load("support-agent-1.4.1"), manifest)
print(report.render())
```

```bash
rewyn manifest support-agent --version 1.4.2 --save
rewyn manifest support-agent --graph
rewyn drift support-agent-1.4.1 support-agent-1.4.2
```

Drift that is **silent**, behaviour changing while every declared dependency
stayed identical, is named as such rather than attributed to a cause. That
usually means a provider-side model update, changed external data, or plain
nondeterminism.

## API reference

`rewyn/runtime/recorder.py`, `rewyn/core/settings.py`,
`rewyn/core/manifest.py`, `rewyn/integrations/otel.py`,
`rewyn/runtime/cancellation.py`.

## Failure modes

**Events are missing.** The recorder dropped them under pressure, or the
process exited before a flush. `recorder.dropped` counts the first;
`flush()` fixes the second.

**Disk fills up.** Event logs are the bulk of it. Rotate `REWYN_HOME` or
sync and prune.

**Cost reads as zero.** No price registered for that model.

**Recording slows the app.** It should not; `emit` is a queue put. If it
does, you are emitting enormous payloads. Keep large blobs out of state and
out of tool results.

**A run has no `output`.** Set `run.manifest.output` when you own the run
directly. Agents and graphs do it for you.
