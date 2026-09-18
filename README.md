# Rewyn

**Build. Run. Replay. Improve AI.**

Agents fail in ways tests do not catch. They worked yesterday, they answer
differently today, and the run that proves it is gone.

Rewyn is a flight recorder for AI agents. Every run is recorded locally, so a
failure you cannot reproduce can be replayed exactly — offline, with no
provider call — then diffed against a working run and turned into a
regression test.

It is provider-agnostic, framework-independent and local-first: no account,
no Rewyn API key, no network.

![Rewyn demo](demo/rewyn-demo.gif)

```bash
pip install rewyn
```

## Start where you are

**You already have an agent.** Keep it. `instrument` wraps any callable so
each invocation becomes a recorded run — your framework does not need to know
Rewyn exists, and Rewyn does not import it.

```python
from rewyn.integrations.frameworks import instrument

# A LangChain, LlamaIndex, CrewAI or plain-SDK agent you already run.
agent = instrument(langgraph_app.invoke, framework="langgraph", version="3")

answer = agent(question)  # now a recorded Rewyn run
```

That records the boundary — input, output, duration, failure, and the version
of what ran. Rewyn sees the edges of a foreign agent, not its internals, and
does not pretend otherwise. Anything inside that uses Rewyn primitives is
recorded in full.

**You are building one.** Then every primitive is recorded, and the detail is
complete:

```python
from rewyn import Agent, tool


@tool(risk_level="low")
def ev_market_share(region: str) -> dict[str, float]:
    """EV share of new car sales, by region."""
    return {"region": {"europe": 24.5, "china": 41.0}.get(region.lower(), 0.0)}


agent = Agent(model="anthropic:claude-opus-5", tools=[ev_market_share])
result = agent.run("Compare EV adoption in Europe and China")
```

Model calls, tool calls, retrieval, memory reads, MCP calls, skill loads, loop
iterations, handoffs, subagents, checkpoints, approvals and guardrails each
emit a structured event. That event log is what makes everything below
possible.

## Replay a run you cannot reproduce

Replay reconstructs a recorded run from its events, serving the recorded
responses back in order. No API key, no spend, no network — you can step
through a production failure on a plane.

```bash
rewyn replay run_01M2SG0RJYWGRPGA77KAEM6D40
```

```
replay    run_01M2SFSB5BAMW6HZPMCWJJ6XH4
of        run_01M2SG0RJYWGRPGA77KAEM6D40
mode      reconstruct
model     recorded   tools recorded
identical True   faithful True
swapped   5 recorded response(s)
```

It reports whether the reconstruction was faithful rather than quietly
diverging. Replaying against a live model instead, and diffing the two, is how
you see what a provider changed underneath you.

## Turn the failure into a regression test

The run that broke becomes a case. Change the prompt, the model or a tool,
re-run the set, and see what moved — with the old behaviour on record to
compare against.

```python
from rewyn.evaluation.dataset import Dataset
from rewyn.evaluation.metrics import exact_match
from rewyn.evaluation.regression import run_regression

dataset = Dataset(name="critical")
dataset.add_run(result.run_id)

report = run_regression(dataset, agent, evaluators=[exact_match()])
print(report.render())
```

## Console

```bash
pip install "rewyn[ui]"
rewyn ui                          # http://127.0.0.1:4400
```

An execution debugger for your own runs: the timeline of everything an agent
did, the execution graph, and a context inspector that shows which item came
from where, at which version, and how much of the budget it cost. No account
and no cloud — it reads the `.rewyn/` you already have. The cloud console
serves the same screens over a team's runs. See [docs/ui.md](docs/ui.md).

## See it work

```bash
uv run python -m demo
```

A minute, offline, no API key. A support agent that works; the same desk
built full stack with retrieval, memory, MCP, a subagent, a graph and a
hostile customer email that cannot override policy; the same agent giving a
different answer the next day; and the diff, replay and release gate that
catch it. See [demo/](demo/).

## Install

```bash
pip install rewyn                # core, provider agnostic
pip install "rewyn[anthropic]"   # Anthropic adapter
pip install "rewyn[openai]"      # OpenAI adapter
pip install "rewyn[google]"      # Google Gemini adapter
pip install "rewyn[mcp]"         # Model Context Protocol client/server
pip install "rewyn[ui]"          # the local console (rewyn ui)
pip install "rewyn[redis]"       # shared memory and checkpoints
pip install "rewyn[s3]"          # S3-compatible object storage
pip install "rewyn[search]"      # Elasticsearch / OpenSearch retrieval
pip install "rewyn[all]"         # everything
```

Rewyn is local-first: no account, no cloud, no Rewyn API key. Local state
lives in `.rewyn/`.

## CLI

```
rewyn init | runs | inspect | replay | diff | eval | test | datasets
        | manifest | drift | export | import | doctor | skills | mcp | agents
        | ui | login | logout | sync
```

## What the SDK guarantees

- **Everything is an event.** Every primitive emits a structured execution
  event, which is what makes replay, diff, evaluation and regression possible.
- **Provider agnostic.** No module outside the provider adapter imports a
  provider SDK or assumes its message format. Provider-specific capabilities
  are an explicit `provider_options={}` escape hatch, never hidden.
- **Local first.** `pip install rewyn` is fully usable with no account and no
  cloud.
- **Never breaks the host app.** Telemetry is async, batched, buffered and
  fails open. If the recording layer throws, your agent keeps running.
- **Secrets never persist.** Keys, tokens and private keys are redacted before
  anything is written or uploaded, and untrusted context cannot override
  trusted policy.
- **Versioned for provenance.** Prompts, skills, tools, agents, graphs,
  context configs and datasets are versioned, so a historical run can be
  reconstructed exactly.

## Cloud (optional)

Rewyn is complete without it, and always will be. When a team wants shared
runs, centralised datasets and evaluation history that outlives one laptop,
the SDK syncs to a hosted service:

```bash
rewyn login --endpoint https://api.rewyn.dev --key rw_...
rewyn sync
```

The client half of that protocol is here and open source, so what a
deployment speaks stays inspectable. Uploads retry, buffer locally when the
service is unreachable, and never raise into your application. See
[docs/cloud.md](docs/cloud.md) and <https://rewyn.dev>.

## Documentation

[docs/](docs/) has a page per subsystem, each with a concept, a minimal
example, a production example, an API pointer and the failure modes. Start
with [getting started](docs/getting-started.md).

The golden demo is one application that uses every subsystem and then
replays, diffs, evaluates and gates itself, offline:

```bash
uv run python -m examples.enterprise_research_agent
```

Rewyn also works alongside what you already run: Redis, PostgreSQL with
pgvector, S3, Elasticsearch, OpenTelemetry, other agent frameworks, and
other agents over an agent-to-agent interface. See
[integrations](docs/integrations.md).

## Development

```bash
make install   # uv sync with all extras
make check     # ruff + mypy --strict + pytest
```

[`CONTRIBUTING.md`](CONTRIBUTING.md) if you would like to help.

## Status

Rewyn is 0.1 and early. The API has not yet met enough users to promise
backwards compatibility on every public symbol, so it goes out as 0.x until
it has. Issues and pull requests welcome.

## License

Apache-2.0.
