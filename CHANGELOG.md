# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
semantic versioning.

## [0.1.0] - 2026-09-16

The first release under the name Rewyn, and the first intended for the public.

The project was built as Runwise, but that name was unavailable everywhere it
mattered: `runwise` on PyPI is an unrelated ML run-analysis package, the GitHub
handle is held, and Runwise is a funded building-heating company holding
runwise.com. Rewyn is clear on PyPI, GitHub and rewyn.dev, and reads as
"rewind" — which is what this SDK does.

The version resets from 1.1.0 to 0.1.0 deliberately. Everything below this
entry was pre-release development under the old name, none of it published to
an index. A 1.x number is a promise of backwards compatibility on every public
symbol, and this API has not yet met a user. It goes out as 0.x until it has.

### Changed

- The distribution, module, CLI and state directory are `rewyn`; the eighteen
  environment variables are `REWYN_*`; the base exception is `RewynError`.
- `Homepage` is now rewyn.dev, with the repository, changelog and issue
  tracker listed separately so PyPI links resolve where readers expect.

### Fixed

- `rewyn doctor` crashed on a default `pip install rewyn`. It probes for
  provider extras with `find_spec`, which imports the parent of a dotted
  name, so a missing `google` raised instead of reporting the extra as
  absent. Development always installed every extra, so only a plain install
  ever hit it — which is most of them.

## [1.1.0] - 2026-09-15

The console had no answer for the person who has not recorded anything yet.
Every one of the UI specification's 63 sections describes reading something
that already exists, so the product was excellent at inspecting a system you
have and silent about how to get one. On a fresh install it opened on five
zeros, three empty lists and twenty-six navigation entries, none of which
explained what the tool was for or what to do first.

### Added

- A first screen (UI §47). A console with no runs opens on it instead of an
  empty dashboard: what Rewyn is in one paragraph, where it is reading
  from, the path from nothing to a useful console with each step ticked from
  what the project actually contains, and the four jobs the tool is for —
  debug an answer you do not trust, prove a change did not make it worse,
  find what changed outside your code, understand what it costs — each
  linking to the screen that does it and each honest about what it still
  needs.
- A demo project, loadable from that screen or with `rewyn demo load`. An
  inspection tool is worth nothing until you have recorded something, and you
  cannot tell whether recording is worth it until you have seen the tool;
  this breaks that circle. Two agents across two versions, a cluster of
  identical failures, an evaluation and a dataset, recorded locally with no
  API key, so incidents, drift, releases, evaluations and the registry pages
  all have something real to show. Every run it writes is tagged `demo` and
  removal is scoped to that tag, so exploring the product cannot be confused
  with — or delete — your own data. There is a test for exactly that.
- `rewyn quickstart`, writing a first agent that runs as written: no API
  key, no placeholder to fill in, and a recorded run at the end of it. The
  old empty state offered a snippet with a literal `...` in it, which
  produced a run with nothing in it.
- An answer to "how do I create a project", which is that you do not. A
  project is a directory with a name locally and a provisioned tenant in the
  cloud. The sidebar now shows which one you are looking at and the path or
  endpoint it is reading, and says how to point it somewhere else.
- `GET /v1/onboarding` on both surfaces, and `POST`/`DELETE /v1/demo` on the
  local one. The cloud declines to seed demo data into a shared project and
  says so rather than hiding the button.

### Changed

- Navigation entries whose screens have no data yet are marked rather than
  hidden — the sidebar is the map of the product, and a map that erases the
  places you have not been to is a worse map. Hovering one says what would
  unlock it. The marker is hidden from the accessible name, so the entry is
  still called "Datasets".
- `Capabilities` carries the project location and the counts the navigation
  needs, so the shell can mark entries without a second request.

## [1.0.1] - 2026-09-15

An audit of the whole UI development plan against the tree, and every gap it
found closed. No new screens; the product is the same, and now the plan is
true about it.

### Added

- The seeded project the plan asked for in U0 and never got:
  `tests/ui/fixtures.py` produces 100,000 runs, a 5,000-event run with a
  200-item context assembly, a 40-node graph and a 2,400-case dataset.
- `tests/ui/test_performance.py` asserts all eight UI §50 budgets against it,
  in `make check`. Seeding costs about three seconds, so the budgets run in
  the main suite rather than behind a marker. Three budgets are browser
  numbers a pytest cannot measure ("first row painted", "60fps",
  "event-to-paint"); each of those tests holds the server to the half it owns
  and says so rather than pretending. Observed against the budgets: first page
  of 100k runs well inside 800ms, p95 across the §7 filter set inside 150ms,
  and a 176KB gzipped first route against a 250KB ceiling.
- `GET /v1/releases/:id` and `POST /v1/releases/:id/promote` on both surfaces
  (UI §43), which P3 named and never built. A release is addressed as
  `<application>@<version>`. Promoting records who authorized which version
  for which environment and the regression report it rested on; it is refused
  with 409 and the failing gate when the release is `blocked`, and refused for
  `unverified` too, because unverified is not the same as safe. The refusal is
  server-side on both surfaces, so the disabled button is a courtesy and not
  the control (UI §54). In the cloud it needs `write` and lands in the audit
  log. The console still does not deploy your code — your pipeline reads the
  decision instead of guessing.
- `REWYN_ENV` / `Settings.environment` (UI §42), which P0 specified. Every
  run now carries an environment without the caller passing metadata, and a
  run that sets one explicitly still wins — the run knows better than the
  shell does.
- The UI §57 first-milestone review, recorded in `docs/ui.md` as the plan
  required before P2: the review against §2, §3, §59 and §61, what it found,
  and the three things that changed because of it.
- Prettier, which §2 named and which was never installed. `make check-web` and
  CI now check formatting alongside lint, types and tests.

### Changed

- The plan now describes what was built. Four decisions that were made
  silently during P0 are recorded with their reasoning: no Tailwind, Radix,
  TanStack Query or Zustand (D-9); a hand-written, contract-tested TypeScript
  mirror instead of a generated client (D-10); system fonts and a two-pane
  frame instead of webfonts and three panes (D-11). UI §51 calls React,
  TypeScript and Next.js "recommended" and the tree "suggested", so these were
  the plan's choices rather than the spec's — but choices worth keeping are
  still worth writing down.

## [1.0.0] - 2026-09-13

P4 of the UI specification: the control room. The loop UI §60 draws --
build → debug → replay → experiment → evaluate → regression test → release →
monitor → learn → build -- is now navigable without leaving the console, which
was the gate on this phase and on the product.

### Added

- Incidents (UI §36). A failure cluster is an incident: several runs of an
  agent failing the same way inside a day, or a jump in average cost. Nobody
  declares one; the console computes them from the runs. Each gets the
  seven-step timeline the spec draws -- deployment, behavior change,
  detection, investigation, fix, regression, resolved -- and all seven are
  always shown. A step is reached only when something recorded reached it: the
  dependency version that moved before the first failure, the run that failed,
  the person who took it, the regression report that covers it, the clean runs
  since. A step nothing reached prints what would reach it, so the timeline is
  also the to-do list. The likely cause is labelled Observed or Inference, and
  when nothing moved before the failures started the page says that instead of
  reaching for a cause.
- The AI development workspace (UI §37). Configuration, run, context, memory,
  MCP, skills, tools, replay, evaluation and regression for one agent in a
  single frame, with §60's loop as a strip above it. Every band carries the
  state read from what the agent actually recorded, and `next_step` names the
  first band that is broken or missing -- the screen answers "what now?"
  rather than leaving it to be worked out. The nine loop steps are also in the
  command palette, so any of them is one keystroke from any screen.
- AI-assisted debugging (UI §23). "Explain difference" writes the paragraph
  the spec gives as its example. It is written by an explanation agent, which
  is a Rewyn agent -- recorded, costed and replayable like the runs it
  explains, and linked from its own answer. The agent is handed a numbered
  ledger of what the comparison observed and nothing else, and must cite an id
  for every sentence; a sentence citing nothing is removed before the reader
  sees it and shown as removed. It is opt-in through
  `REWYN_EXPLAIN_MODEL`, because it costs a model call, and without it the
  console composes the same narrative from the same evidence by rule and says
  so. An explanation works on a laptop with no API key.
- Collaboration (UI §45). Comment threads on runs, incidents, datasets,
  agents and releases; named saved views of a filter; assignment and status on
  incidents. These are the only things in the console a person wrote rather
  than an execution produced, so locally they get their own durable store
  rather than a table in the index, which is dropped whenever the projection
  version moves. In the cloud they are project-scoped rows: the team sees one
  thread, writing needs the `write` permission, and another project sees none
  of it.

### Changed

- Incident detection moved out of `rewyn.ui.aggregates` into
  `rewyn.ui.incidents`, and incident ids are now a hash of the failure
  rather than `hash()`. Python salts string hashing per process, so the old
  ids changed on every restart -- fine for a row on the overview, wrong for
  something that is assigned, commented on and linked to.
- `rewyn.ui` names its own modules, so `hasattr(rewyn.ui, "incidents")`
  is true before anything has imported it. The lazy-import rule now holds for
  introspection as well as for imports.
- The sidebar gains a separate group at its foot for Workspace and Incidents.
  UI §4's list is unchanged, character for character, and the test that
  guards it still does; the two screens §36 and §37 name needed an entry
  point, and a labelled addition is better than a quiet edit.

## [0.10.0] - 2026-09-13

P3 of the UI specification: what could have changed outside your code, and
whether it is safe to deploy. Every screen in the §4 navigation now exists.

### Added

- The dependency map (UI §31): drawn from what runs recorded, with a second
  level only where a relationship was observed -- the tools an MCP server
  exposed, the tools a skill declared. Everything else hangs off the agent,
  because nothing claimed it. Every node clicks through to its BUILD page.
- Drift (UI §32): two windows of an agent's runs, the success rate it used to
  have against the one it has now, and a cause for each dependency whose
  recorded version moved. When behaviour moves and nothing underneath it did,
  the page says so -- that is the finding, not a gap.
- Cost (UI §33), answered in cost per successful task rather than cost per
  run, broken into the five categories the SDK accounts for, grouped by
  agent, model, user, tool, skill, MCP server, environment or day.
- Releases (UI §43): each agent version, what changed since the previous one,
  its regression result, and a status. `ready` needs a report that says so;
  without one a version is `unverified`, which is not the same as safe.
  Promotion stays in your deploy pipeline, and the console says so.
- Experiments (UI §28): control against variants on one dataset, with the
  metrics each arm measured.
- Notifications (UI §40): regressions, drift, dependency changes, cost spikes
  and failure clusters, one per cause. Reached from the shell rather than a
  new navigation entry, because §4's sidebar does not have one -- as is the
  cost page, from the number that raises the question.
- Cost categories are stored per run on both surfaces, so the cost page never
  reads an event log.

### Fixed

- The execution graph and the dependency map were drawn as `role="img"` while
  their nodes were buttons, which makes the buttons presentational and their
  focus a nested-interactive violation. Both are groups now, and the graph
  tab has its own accessibility check.
- `BehaviorManifest.from_runs` refused a `RunManifest`, which is all it needs
  from a run.

## [0.9.0] - 2026-09-13

P2 of the UI specification: what the AI is made of, whether it is any good,
and what it is doing right now.

### Added

- The BUILD pages (UI §4): agents, models, prompts, skills, tools, MCP,
  context, memory and graphs. None of it is configured or registered --
  every entry is rolled up from the dependencies runs already record, so a
  page describes what actually ran.
- Agent pages and version history (UI §29, §30), including a v17 → v18
  comparison that diffs the behaviour manifests those versions produced.
- Evaluations (UI §27): one score distribution per evaluator rather than one
  number, over the verdicts the evaluation subsystem records.
- Regression (UI §26): the reports `rewyn test` produces, baseline beside
  candidate, and the cases that became worse -- each linking to its run. The
  New experiment form composes the command rather than pretending the
  console can execute your agent.
- Live mode (UI §34) and its Server-Sent Event streams (UI §53). A run's
  manifest is written when it starts and its events are flushed as it goes,
  so a console in another process can watch an execution happen with nothing
  extra running.
- `rewyn.human.inbox`: `ApprovalInbox` and `InboxHandler`, an approval
  queue two processes share through `.rewyn/`. An agent asks, the console
  answers, and the decision becomes part of the run (UI §35). A request
  nobody answers is rejected when it times out, so an unattended agent fails
  closed.
- Console endpoints for all of it on both surfaces, except the approval queue:
  an approval is answered where the agent waits, so the cloud does not claim
  to hold one.

### Fixed

- A Server-Sent Event stream kept polling after its reader had gone, holding
  one of the browser's few connections per host until it timed out; the next
  page that reader opened waited behind it. Streams now stop when the client
  disconnects, and a quiet stream lets go rather than holding a socket. The
  browser suite went from fifteen minutes back to eleven seconds.
- The console's index could be read while it was being rebuilt, because a
  screen opens several requests at once and routes run in a thread pool. The
  refresh is locked, so a cold start no longer serves an empty page.

## [0.8.0] - 2026-09-13

P1 of the UI specification: the console can now change AI behaviour and prove
the result. This completes the first-milestone workflow §57 gates on --
runs, run detail, timeline, context, tools, replay, compare, save as test.

### Added

- The replay workspace (UI §20, §21): nine controls, four modes each, over a
  plan the server produces before anything runs. Every control states in
  plain language what it will actually do, and the ones this surface cannot
  honour say why rather than failing later.
- The compare workspace (UI §13, §22, §23): what changed, dimension by
  dimension, with cost, latency and quality deltas; then the observed
  differences; then the hypotheses, labelled Inference and carrying their
  confidence. The two are never merged.
- Save as Test (UI §24) and the dataset screens (UI §25). A run becomes a
  regression case with an expected outcome, an evaluator and a severity, and
  the case remembers the run it came from.
- Console endpoints for all of it, on both surfaces: `replay:plan`, `replay`,
  `replays/{id}`, `diff`, `comparable`, `save-as-test` and `datasets`.
- `replay(..., temperature=..., system=...)`: the two things developers vary
  most, applied to a recorded transcript without rebuilding it.
- Evaluation scores now reach the runs they judge. An evaluator records its
  verdict on the evaluating run, so both surfaces join it back onto the
  subject: the §7 evaluation-score filter is live, and a run shows its score.
- The keyboard shortcuts §38 names: `R` replay, `C` compare, `T` save as test.

### Fixed

- The navigation told assistive technology the wrong current page on any deep
  link. The bundle is prerendered at `/` and React patches mismatched content
  during hydration but not mismatched attributes; the router now reads the
  location through `useSyncExternalStore`, which is the contract that covers
  this. A browser test guards it.
- The compare table listed output, cost and latency twice: once as diff
  dimensions and once as the rows §22 gives them, with deltas.
- An evaluator did not record itself as a dependency of its own run, so
  nothing downstream could tell that a run contained verdicts.

## [0.7.0] - 2026-09-13

The console: the P0 slice of the UI specification. Rewyn can now be looked
at, not only read back through the CLI.

### Added

- `rewyn ui` serves a console on `http://127.0.0.1:4400` against the
  developer's own `.rewyn/` -- no account, no key, no cloud (UI §44).
  It needs the new `ui` extra; the core install stays lean.
- A console API at `/console/v1`, served identically by the local server and
  by `rewyn-cloud` (UI §52). One contract, two implementations: the wire
  models live in `rewyn.ui.schemas` and both surfaces import them.
- `rewyn.ui.projections`: the read model behind every run-detail panel --
  timeline, execution graph, context, model, prompt, memory, tools, MCP,
  skills, guardrails and approvals -- derived from a run's manifest and event
  log. The local server computes them on demand; the cloud computes them once
  at ingest, so opening a run never scans an event log (UI §50).
- The screens §63 lists for P0: app shell and navigation (§4), global search
  (§5) and command palette (§39), overview (§6), runs with all fourteen
  filters (§7), run detail (§8), timeline (§9), execution graph (§10) and the
  context inspector with its budget composition (§11, §12), plus sessions,
  environments and settings.
- A runs index: SQLite locally, denormalised columns and a facet table in the
  cloud, both refreshed from manifests so no filter reads an event log.
- Environment and user as reserved run metadata, surfaced as first-class
  columns and filters (UI §7, §42).
- `web/`: the console frontend (React, TypeScript, Next.js), built once as a
  static bundle and shipped inside the wheel. Its committed build is checked
  against a fresh one in CI.

### Changed

- `provenance_report` now records each context item's title, URI, tokens,
  relevance, sensitivity and permissions, so the context inspector can show
  what §11 asks for and withhold what §54 says it must.
- The cloud service's request dependencies moved to `rewyn_cloud.deps`,
  shared by the ingest API and the console API.

### Security

- Context items above the caller's role ceiling are returned as withheld
  placeholders rather than dropped: the shape of the context stays honest
  without exposing its content (UI §54).
- The local console answers loopback only, and refuses a request whose `Host`
  is anything else (UI §44).

## [0.6.0] - 2026-09-13

Closes every gap found reviewing the implementation against all 63 sections
of the specification.

### Fixed

- Memory namespaces did not isolate. `Memory` stored the namespace and never
  passed it to the store, so two tenants sharing one `FileStore` read each
  other's items. Items now carry their namespace and reads, deletes and
  clears refuse to cross it. The memory and security docs described this as
  working and have been corrected.
- The audit log ANDed team and project scope, which hid exactly the
  team-level entries an access audit exists to read.

### Added

- Cost accounting for all five categories §40 lists, not just models. Tool
  calls, embedding batches, retrieval queries and sandbox seconds are priced
  and recorded where they are incurred.
- Recording sampling (§53), per run rather than per event so a retained run
  stays replayable. Failed runs are buffered and promoted in full.
- Tool streaming and `Graph.astream`, completing the five things §41 lists.
- Schema migration utilities (§58), applied on read, so a run recorded by an
  older build still loads. Skills gained the schema version §58 asks for.
- Data backends (§51): Redis memory and checkpoints, S3 object storage,
  Elasticsearch and OpenSearch retrieval, and pgvector.
- Framework interoperability (§3.2, §51): export tools to any framework,
  import theirs, and record a LangGraph, CrewAI or LlamaIndex run as a
  Rewyn run. Nothing imports another framework.
- Agent-to-agent interfaces (§51): an agent card, a JSON server, and a
  client that makes a remote agent an ordinary tool with the caller's run id
  carried across the hop.
- Container sandboxing (§22) with every capability dropped, a read-only
  root and a kernel-enforced memory cap.
- Cloud teams, four ordered roles, SSO domain mapping, an audit log,
  retention windows, alerts and dashboard statistics (§45).
- Performance tests for large context, many tool calls, long-running agents
  and large graphs (§55).
- `rewyn eval` accepts a dataset as well as a run, as §48 specifies.
- A documentation page for integrations.

## [0.5.0] - 2026-09-13

The first complete release. Every subsystem in the specification is built,
tested and documented, and the golden demo exercises all of them end to end
offline.

### Added

- Repository foundation: packaging, lint, type-check and test tooling, CI.
- v0.1 core: runs, spans, the canonical event model (spec §42), versioned
  state, canonical JSON fingerprints and JSON Schema helpers.
- Unified model abstraction with OpenAI, Anthropic, Google Gemini,
  OpenAI-compatible and local adapters; streaming, tool calling, structured
  output validation, usage and cost accounting; `FakeModel` for tests.
- First-class tools (`@tool`), registry, permission policies and an
  event-emitting executor.
- `Agent` with a ReAct loop, iteration/token/cost/time budgets, custom stop
  conditions and structured final output.
- Secret redaction, local `.rewyn/` storage and an async, batched,
  fail-open recorder.
- Portable run bundles (`rewyn export` / `rewyn import`).
- CLI: `init`, `runs`, `inspect`, `export`, `import`, `doctor`.
- v0.2 context engineering: `Context(budget=...)` with provenance, trust
  levels, priority ranking, exposed budget decisions, freshness, compression
  and untrusted-content boundaries.
- Memory: working/short-term/episodic/semantic/procedural kinds over
  in-memory and file providers, with `MEMORY_READ`/`MEMORY_WRITE` events and
  context integration.
- RAG: chunkers, deterministic hashing embedder, vector and BM25 retrievers,
  rerankers and an observable `RAGPipeline`.
- MCP: first-class client (stdio, HTTP, in-process), tool adapter, server
  helper, registry and config discovery; MCP tool calls flow through the
  tool executor as events.
- Skills: `SKILL.md` loader, discovery, registry, progressive disclosure
  with `load_skill`/`read_skill_file` tools and `SKILL_LOADED`/`SKILL_ACTIVATED`
  events.
- `Agent(context=..., memory=..., skills=..., mcp=...)` integration.
- CLI: `skills`, `skills show`, `mcp`, `mcp tools`.
- v0.3 graphs: `Graph` with sequential, conditional, routed, looping and
  parallel edges, retries, timeouts, subgraphs, agent nodes, approval gates,
  checkpoints and resume; `GRAPH_*`/`STATE_UPDATED` events.
- Loop strategies `plan_execute` and `reflection`, evaluator termination,
  lifecycle hooks, subagents (`SUBAGENT_*`), handoffs (`HANDOFF`).
- Runtime: checkpoints (in-memory and file stores), cancellation tokens,
  executor timeouts, unified event streaming (`Agent.astream`), subprocess
  sandbox with `SANDBOX_EXECUTED` events and a `run_code` tool.
- Guardrails: PII, prompt injection, prohibited content, schema, length,
  callable and model-judged checks with recorded decisions; agent
  integration for input and output.
- Human-in-the-loop: `human.approve`, handlers (auto, callback, console,
  queue), tool approvals, feedback, corrections and escalation events.
- CLI: `agents`.
- v0.4 replay: deterministic reconstruction of a recorded run, re-execution
  against a target with recorded models and tools substituted call for call,
  and prompt-level replay of a recording against a different model.
  `ReplayResult` separates `identical` (same output) from `faithful` (also
  the same call sequence), and reports every mismatch.
- Execution diff across the spec §28 dimensions, keeping observed
  `differences` strictly apart from hypothesised `explanations`.
- Evaluation: `@evaluator`, deterministic metrics (exact match, contains,
  regex, JSON and schema validity, tool correctness, error, latency and cost
  budgets) and LLM judges for correctness, relevance, groundedness, style,
  safety and task completion.
- Datasets: any recorded run can be saved as an example; versioned,
  fingerprinted and stored under `.rewyn/datasets/`.
- Regression testing and release gates: `run_regression` with per-metric
  aggregates, baseline comparison, thresholds and a `ReleaseGate`.
- AI dependency graphs, behavior manifests and drift detection, including
  naming silent drift rather than inventing a cause for it.
- Runs now record their input on the manifest, and agents declare every tool
  and guardrail they offer as a run dependency.
- OpenTelemetry export (`rewyn[otel]`), fail-open like the recorder.
- CLI: `replay`, `diff`, `eval`, `test`, `datasets`, `manifest`, `drift`.
- v0.5 cloud: `rewyn-cloud`, a FastAPI service with API-key auth, projects,
  run ingest, search, dataset storage and evaluation records. SQLite by
  default, PostgreSQL through `DATABASE_URL`, and an object-storage
  abstraction with a local filesystem backend for event logs.
- `storage/remote.py`: an SDK-side sync client that uploads run bundles with
  exponential-backoff retry, a local queue for anything that fails, and
  fail-open behaviour so a telemetry upload can never take down the host
  application. Credentials are stored with owner-only permissions and the
  API key is registered with the redactor so it cannot reach a recorded
  event.
- CLI: `login`, `logout`, `sync` (including `--status`, `--pull`,
  `--datasets` and `--evaluations`); `doctor` now reports cloud reachability.
- The quality gate now type-checks and covers the cloud package.
- Golden demo (`examples/enterprise_research_agent/`): one application using
  context, memory, RAG, skills, MCP, a graph, a subagent, tools, guardrails
  and human approval, then replaying, diffing, evaluating and gating itself.
  Runs offline against a scripted model.
- Documentation for all 23 sections in spec §56, each with a concept, a
  minimal example, a production example, an API pointer and failure modes.
  Their Python snippets are checked against the real package by the test
  suite, so a rename cannot leave the docs quietly wrong.
- MCP tools are now given an explicit prefix in the demo and docs, which
  keeps the origin of a tool visible in transcripts and dependency lists.

### Notes

- `pip install rewyn` remains fully usable with no account, no cloud and no
  Rewyn API key. The cloud is a separate package.
- Provider adapters are tested against fake SDK clients and never live APIs.

[0.6.0]: https://github.com/rewyn-dev/rewyn/releases/tag/v0.6.0
[0.5.0]: https://github.com/rewyn-dev/rewyn/releases/tag/v0.5.0
