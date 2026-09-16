# CLAUDE.md

## Project status

The SDK was built in six staged phases, all complete: foundation; core, models,
tools, agent, recording, storage, CLI; context engineering, memory, RAG, MCP
and skills; graphs, loop strategies, subagents, handoffs, checkpoints,
runtime, guardrails and human-in-the-loop; replay, execution diff,
evaluation, datasets, regression testing, behavior manifests, drift and
OpenTelemetry export; the cloud platform with its SDK sync client; and the
golden demo plus the documentation set.

A full review against all 63 spec sections followed, and every gap it found
is closed: cost categories beyond models, recording sampling, tool and graph
streaming, schema migrations, Redis/S3/Elasticsearch/pgvector backends,
framework interoperability, agent-to-agent interfaces, container sandboxing,
cloud teams/roles/SSO/audit/retention/alerts/dashboards, and performance
tests.

The project was renamed from Runwise to Rewyn before its first public
release, and the version reset to 0.1.0: everything built so far was
pre-release development, never published to an index. See CHANGELOG.md.

A second product specification drives the UI. All five of its phases are
complete: runs, run detail,
timeline, execution graph and the context inspector; replay, compare,
save-as-test and datasets; the BUILD registry pages, agent versions,
evaluations, regression, live mode and approvals; dependencies, drift, cost,
releases, experiments and notifications; and the control room — incidents
with the §36 timeline, the §37 development workspace and the §60 loop,
comments and saved views, and the §23 explanation agent. Every screen is
served by both `rewyn ui` and the cloud.

Read the `docs/` page for a subsystem before changing it, and update that page
in the same change. The product specification and the staged build plans are
kept in the private planning repository; this file summarises the decisions
that constrain *how* code should be written here.

## What Rewyn is

A provider-agnostic, framework-independent Python SDK for building, executing,
recording, replaying, evaluating and regression-testing AI agents — plus a cloud
platform layered on top. Tagline: *Build. Run. Replay. Improve AI.*

## Architectural rules that apply to all code

These come from the spec and should be treated as invariants, not preferences:

- **Everything is an event.** Every primitive (model call, context retrieval,
  memory read, tool call, MCP call, skill load, loop iteration, handoff,
  subagent, checkpoint, human approval, guardrail) emits a structured execution
  event. See spec §42 for the event names. This is what makes replay, diff,
  evaluation and regression possible — a primitive that does not emit events is
  incomplete.
- **Provider agnostic.** No module outside `rewyn/models/<provider>.py` may
  import a provider SDK or assume a provider's message format. Provider-specific
  capabilities are exposed through an explicit `provider_options={}` escape
  hatch rather than hidden.
- **Modular imports.** `from rewyn import model` must work without pulling in
  the agent, RAG, or cloud stack. Keep subpackage imports lazy; avoid top-level
  re-exports that force transitive imports.
- **Local-first.** `pip install rewyn` must be fully usable with no account, no
  cloud, and no Rewyn API key. Local state lives in `.rewyn/`.
- **Instrumentation must never break the host app.** Telemetry is async,
  batched, buffered locally, and fails open. If Rewyn's recording layer throws,
  the user's agent keeps running.
- **Never persist secrets.** API keys, tokens, passwords and private keys must be
  redacted before anything is written to disk or uploaded. Context items carry
  `trust_level` / `authority` / `sensitivity` / `provenance`; untrusted content
  must not be able to override trusted policy.
- **Versioning and provenance.** Prompts, skills, tools, agents, graphs, context
  configs, MCP configs and datasets are all versioned so any historical run can
  be reconstructed exactly.

## Package layout

Spec §5 defines the intended tree. Follow it when creating new modules rather
than inventing a parallel structure:

```
rewyn/{core,models,agents,context,memory,rag,tools,mcp,skills,graphs,
         runtime,guardrails,human,replay,evaluation,integrations,storage,
         security,cli}/
```

The cloud service is a separate `rewyn-cloud` package (a uv workspace
member under `cloud/`) so the open-source boundary in spec §47 stays a
package boundary, not a convention.

`rewyn/ui/` holds the console API, its wire schemas and the read model that
turns a recorded run into the console's panels; `rewyn_cloud.console`
serves the same routes over the database. The frontend is `web/` (React,
TypeScript, Next.js, UI spec §51), built once into `src/rewyn/ui/static/`
and committed so `pip install rewyn[ui]` needs no Node.

## Build order

Do not attempt to build the whole SDK at once. Spec §59 stages it:

| Version | Scope |
| --- | --- |
| v0.1 | model abstraction, Agent, tools, runs, events, recording, local storage, CLI |
| v0.2 | context, RAG, memory, MCP, skills |
| v0.3 | graphs, loops, subagents, handoffs, state, checkpoints |
| v0.4 | replay, diff, evaluation, regression |
| v0.5 | cloud |

When asked to implement a later-stage feature, check whether its dependencies
from earlier stages exist first.

## Commands

```bash
make install     # uv sync --all-extras --all-packages
make check       # ruff check + ruff format --check + mypy --strict + pytest
make lint        # ruff only
make typecheck   # mypy only
make test        # pytest with coverage
uv run rewyn --help
uv run python examples/quickstart.py
uv run python examples/replay_and_regression.py
make demo                                            # the four-act demo
make record                                          # re-record it (needs vhs)
uv run python -m examples.enterprise_research_agent   # the golden demo
uv run rewyn-cloud create-project acme && uv run rewyn-cloud serve
uv run rewyn ui                                    # the local console
make build-web                                       # rebuild the console bundle
make check-web                                       # console lint, types, unit tests
make e2e                                             # browser tests, real server
```

Tests live under `tests/<subpackage>/` and use `rewyn.testing.FakeModel`;
provider adapters are tested against fake SDK clients, never live APIs. The
autouse `rewyn_home` fixture isolates `REWYN_HOME` per test. Cloud tests
(`tests/cloud/`) drive a real FastAPI app over an in-process ASGI transport
with in-memory SQLite and object storage, so they need no server and no
network. `mypy` and coverage both include `cloud/src`.

## Notes for working in this repo

- `main` is the default branch. Commit at the end of each plan phase.
- Docs live in `docs/<subsystem>.md`, one page per spec §56 section. Their
  Python snippets are parsed and checked against the package by
  `tests/examples/test_docs.py`, so renaming a parameter breaks the docs test
  rather than the docs silently.
- Follow the spec's example snippets for API shape and naming (`Agent(...)`,
  `Context(budget=...)`, `@tool`, `@evaluator`, `models.OpenAI(...)`).
- Async-first core with sync facades via `rewyn.core.sync.run_sync`.
- Lazy imports inside functions are deliberate (modularity rule); ruff's
  PLC0415 is disabled for that reason.
- Prefer real, working slices over scaffolding: one primitive that records
  proper events end-to-end is worth more than many empty module stubs.
