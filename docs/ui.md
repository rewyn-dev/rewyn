# Console

## Concept

Everything Rewyn records is structured, which means it can be read back as
something better than a log. The console is that reading: an execution
debugger, a context inspector and a replay lab, built for the moment when an
agent that worked yesterday is answering badly today.

It is one product with two surfaces. `rewyn ui` serves your own
`.rewyn/` on localhost with no account and no key, because the
open-source workflow has to be complete on its own. The cloud console serves
the same screens over a team's centralised runs, adding what a team needs and
an individual does not: roles, shared datasets, audit, retention.

Both serve the same API, from the same code. A run-scoped view -- the
timeline, the context inspector, the tools panel -- is a *projection*: a small
document derived from the run's manifest and event log by
`rewyn.ui.projections`. The local server computes projections on demand; the
cloud computes them once at ingest and stores them. There is one
implementation, so the two surfaces cannot drift, and no screen ever has to
load a whole run to draw a header.

```text
.rewyn/ ────┐                      ┌─ rewyn.ui.projections ─┐
            ├── rewyn.ui.server ───┤                        ├─→ /console/v1
PostgreSQL ─┘   (cloud console)    └─ rewyn.ui.schemas ─────┘
```

## Your first five minutes

The console is derived entirely from recorded runs, so there is nothing to
configure and nothing to create in the UI. A project is a directory
(`$REWYN_HOME`, default `./.rewyn`) with a name (`$REWYN_PROJECT`); it
exists as soon as a run is recorded into it. In the cloud a project is
provisioned once with `rewyn-cloud create-project <name>`, which prints the
key your agents sync with.

```bash
pip install "rewyn[ui]"
rewyn quickstart          # writes a runnable first agent
python quickstart.py        # records a run into .rewyn/
rewyn ui                  # read what it did
```

A console with no runs in it opens on a first screen instead of an empty
dashboard: what Rewyn is, where it is reading from, the path from nothing
to a useful console with each step ticked from what the project actually
contains, and the four jobs the tool is for.

If you would rather look before you instrument anything:

```bash
rewyn demo load           # two agents, two versions, a failure cluster,
                            # an evaluation and a dataset — recorded locally
rewyn demo clear          # and gone again
```

The same button is on the first screen. Every demo run is tagged `demo` and
clearing removes exactly those, so exploring the product cannot be confused
with — or delete — your own data.

## Minimal example

Record something, then look at it:

```python
from rewyn.core.run import start_run
from rewyn.runtime.recorder import default_recorder

with start_run("support") as run:
    run.manifest.output = "handled"
default_recorder().flush()
```

```bash
rewyn ui
```

The console opens on `http://127.0.0.1:4400`, listing every run in
`.rewyn/`. It answers requests from this machine only.

## Production example

In production the console is the cloud one, and what it can show depends on
what the run recorded. Two pieces of metadata make the difference between a
run you can find and a run you can only scroll past: the environment it ran
in, and who it ran for.

```python
from rewyn.core.run import start_run

with start_run(
    "refund",
    metadata={"environment": "production", "user": "acme-support", "session_id": "sess-8812"},
) as run:
    run.manifest.output = "refund approved"
```

Those three keys become first-class columns: the runs table filters by
environment and user, and the sessions view groups runs that belong to one
conversation. Everything else the filters offer -- agent, model, tool, MCP
server, skill -- comes from the dependencies a run already records, so it
needs no extra work.

The index behind the local runs table is a cache, rebuilt from manifests:

```python
from rewyn.ui.index import RunIndex, RunQuery

index = RunIndex()
index.refresh()
page = index.search(RunQuery(agent="refund-agent", status="failed", limit=25))
print(page.total, [run.id for run in page.runs])
```

Deleting `.rewyn/ui/` costs one rebuild and nothing else.

## What each screen answers

| Screen | Question |
| --- | --- |
| Welcome | What is this, and what do I do first? |
| Overview | Is my AI system healthy? |
| Runs | Which execution went wrong? |
| Run detail, timeline, graph | What did it actually do? |
| Context inspector | What information did it use, and what did that cost? |
| Model, prompt, memory, tools, MCP, skills | What was it made of on this run? |
| Replay | Can I reproduce it, and what happens if I change one thing? |
| Compare | What changed between these two runs? |
| Datasets | What must not break again? |
| Agents, Models, Prompts, Skills, Tools, MCP | What exists, at which version, used by what? |
| Evaluations | Is it good? |
| Regression | Did my change make it worse? |
| Live | What is running right now, and does anything need me? |
| Dependencies | What could have changed outside my code? |
| Drift | Is behaviour moving, and what moved underneath it? |
| Cost | What did the spend go on, and what did a successful task cost? |
| Releases | Is it safe to deploy? |
| Experiments | Which variant wins? |
| Incidents | What is broken, since when, and what moved before it? |
| Workspace | Where is this agent in the loop, and what is the next step? |

## Replay, compare, and turning a failure into a test

The console's second half is about changing behaviour deliberately and
proving the result.

**Replay** offers nine controls -- model, prompt, context, memory, skills,
tools, MCP, temperature and system instructions -- each with the four modes
the specification lists. The console plans a replay before running it and
says in plain language what every control will actually do, including the
ones it cannot change: memory reads and MCP responses come from the
recording, and re-executing an agent needs the agent's own code, which the
console does not have. Nothing is claimed that cannot be delivered.

With nothing changed, a replay reconstructs the run exactly: no provider is
called and nothing is spent. Change the model, the temperature or the system
instructions and every recorded request is re-issued, giving a prompt-level
experiment over a real transcript.

**Compare** answers "what changed?" first -- a row per dimension, plus the
cost, latency and quality deltas -- and then separates what it knows from
what it suspects. Observed differences are read from the two recordings.
Inferences are hypotheses, each carrying a confidence, and the console never
merges the two.

**Save as Test** turns the run in front of you into a permanent regression
case: a dataset name, the expected outcome (defaulting to what the run
actually produced), an evaluator and a severity. The case remembers the run
it came from, so a failure investigated once is a test forever.

```python
from rewyn.evaluation.dataset import Dataset

dataset = Dataset.load_or_create("refund-regression")
dataset.add_run("latest", expected="Human-approved refund", severity="critical")
dataset.save()
```

That is the same call the Save as Test dialog makes.

## What your AI is made of

The BUILD pages need no configuration and no registration. Every run records
the dependencies it used -- kind, name, version, fingerprint -- so rolling
those up by name gives the model, prompt, skill, tool and MCP pages, and
rolling them up by version gives an agent's version history. An agent page is
therefore a description of what actually ran, not a description of what a
config file hoped would run.

Comparing two versions of an agent builds a behaviour manifest from the runs
of each and diffs them, which is the same machinery `rewyn drift` uses:

```python
from rewyn.core.manifest import BehaviorManifest, detect_drift

before = BehaviorManifest.from_runs("refund-agent", ["run_a"], version="17")
after = BehaviorManifest.from_runs("refund-agent", ["run_b"], version="18")
print(detect_drift(before, after).render())
```

## Quality

**Evaluations** show one distribution per evaluator rather than one number,
because a mean hides the failures. Scores come from the evaluation subsystem:
anything `@evaluator` records ends up here, attached to the run it judged.

**Regression** lists the reports `rewyn test` produces, with the baseline
beside the candidate and -- the part that matters -- the cases that became
worse. Each one links to the run that produced it.

The console does not run regressions itself. A regression executes your
agent, and your agent is your code; the console has your recordings, not your
application. So the New experiment form composes the exact command instead,
and the report appears on the same screen when it finishes.

## What could have changed outside my code

The dependency map is drawn from what runs recorded, so it shows a second
level only where a relationship was actually observed: the tools an MCP
server exposed, and the tools a skill declared. Everything else hangs off the
agent, because nothing claimed it. Inventing a hierarchy would make the
picture prettier and the answer wrong.

Drift compares two windows of an agent's runs: the success rate it used to
have against the one it has now, and the behaviour manifest of each window
against the other. Every cause is a dependency whose recorded version moved.
When behaviour moves and nothing underneath it did, the page says exactly
that -- the cause is outside the manifest, and that is the finding.

A release is a version of an agent that has run, addressed as
`<application>@<version>`. Its status is `ready` only when a regression report
says so; without one it is `unverified`, which is a different thing from safe.

Promoting records the decision and the evidence behind it — who authorized
which version for which environment, and the report it rested on — and is
refused by the server when the gate has not passed:

```bash
curl -X POST "$API/console/v1/releases/refund-agent@18/promote" \
     -d '{"environment": "production"}'
# 409: refund-agent v18 is blocked: success rate 70% is below the 90% gate.
```

The refusal is server-side on both surfaces, so it does not depend on a
disabled button (UI §54). Deploying is still your pipeline's job; it reads the
decision rather than guessing. In the cloud, promotion needs the `write`
permission and lands in the audit log as `release.promote`.

Cost answers in cost per successful task, not cost per run, because a cheap
run that failed is not a saving. It groups by agent, model, user, tool,
skill, MCP server, environment or day, and breaks the total into the five
categories the SDK accounts for.

## The first-milestone review (UI §57)

UI §57 makes the core workflow a gate rather than a checkpoint: *"if this
workflow feels excellent, expand."* This is that review, recorded against
§2 (design philosophy), §3 (visual direction), §59 (north star) and §61
(product personality). It covers the path the spec names — sidebar → runs →
run detail → timeline → context → tools → replay → compare → save as test.

**§59, the north star.** Every screen on the path answers one of the nine
questions, and the question is written on the screen rather than implied:
Runs answers "what is my AI doing?", the context inspector "what information
did it use?", replay "can I reproduce it?", compare "what changed?". The nav
carries the question for each entry, so an unbuilt screen still says what it
would answer.

**§2 and §46, evidence over decoration.** The overview leads with a status
word backed by the failure rate, not a chart. The only charts in the console
are the context budget bar and the cost breakdown, both of which answer a
question a number could not. Nothing on the path renders a metric it cannot
source from a recorded event.

**§3, visual direction.** Dark first with light available from one token set;
28px rows; a single accent; semantic status colours; motion at 120–180ms and
disabled under `prefers-reduced-motion`. Density was measured against the
spec's own reference points — a run with 5,000 events reads without a scroll
container fighting the page, because the timeline virtualizes.

**§61, personality.** The console says "unverified" rather than "safe", marks
a hypothesis Inference rather than fact, and prints what a missing step would
need instead of going blank. That is the "trustworthy" characteristic doing
work, and it is why the release gate refuses server-side rather than greying
out a button.

**What the review found, and what changed because of it.** Three things:
run detail loaded every panel on open and now loads one per tab (§50); the
comparison offered replays of the same run as comparison candidates, which is
noise, and now excludes them; the empty states named a phase but not an
action, and now carry the button that starts the thing they describe (§47).

**Known deviations.** The frontend uses no component or state library — see
the plan's D-9. The spec asks for React, TypeScript, Next.js and "a
consistent design system", all of which hold.

## Incidents

A failure cluster is an incident: several runs of the same agent failing the
same way inside a day, or a jump in average cost. Nobody declares one. The
console computes them from the runs, gives each a stable id so it can be
assigned and linked to, and draws the timeline UI §36 asks for:

```text
Deployment → Behavior change → Detection → Investigation → Fix → Regression → Resolved
```

All seven steps are always shown, and the interesting part is usually the
ones that are empty. A step is *reached* only when something recorded reached
it -- a dependency version that moved before the first failure, the run that
failed, the person who took it, the regression report that covers it, the
clean runs since. A step nothing has reached prints the sentence that says
what would reach it, so the timeline doubles as the to-do list.

The likely cause is labelled the same way a comparison is: `Observed` for the
dependency that actually moved, `Inference` for the claim that it is the
candidate. When nothing moved before the failures started, the page says so
rather than reaching for a cause.

```python
from rewyn.ui import incidents

found = incidents.detect(runs)  # from RunSummary rows
detail = incidents.detail(found[0], runs=affected, changes=changes)
print(detail.status, [s.stage for s in detail.timeline if not s.reached])
```

## The development workspace

`/workspace?agent=<name>` is UI §37's column -- configuration, run, context,
memory, MCP, skills, tools, replay, evaluation, regression -- as one screen,
with UI §60's loop along the top. Every band carries its own state read from
what the agent recorded: a count, a status, and for a band that has never
happened, what would start it. `next_step` names the first band that is
broken or missing, so the screen answers "what now?" rather than leaving it
to be worked out.

The loop -- build → debug → replay → experiment → evaluate → regression test
→ release → monitor → learn → build -- is also in the command palette
(`⌘P`), so any step is one keystroke from any screen.

## Explaining a difference

The compare workspace already answers *what* changed, structurally. "Explain
difference" adds the paragraph:

```text
The model and prompt remained unchanged. The retrieved refund policy changed
from v18 to v19. This is the strongest observed difference associated with
the changed outcome.
```

It is written by an explanation agent, which is a Rewyn agent: recorded,
costed and replayable like any other run, and linked from the answer. The
agent is handed a numbered ledger of the differences the comparison found and
nothing else, and must cite an id for every sentence it writes. Any sentence
that cites nothing is removed before the reader sees it and listed as
removed, so a model that invents a cause does not get to publish it.

It is opt-in, because it costs a model call:

```bash
export REWYN_EXPLAIN_MODEL=anthropic:claude-opus-5
```

Without it the console still explains the difference -- the same ledger,
rendered by rule rather than written by a model, and labelled
`composed by rule` so you know which you are reading. Local-first means the
explanation works on a laptop with no API key.

## Comments and saved views

A run, an incident, a dataset or an agent can carry a comment thread, and a
filter you found useful can be kept as a named view. These are the only
things in the console that a person wrote rather than an execution produced,
so they are the only things it cannot recompute -- which is why locally they
live in their own `.rewyn/ui/collaboration.db` rather than in the index,
which is dropped and rebuilt whenever the projection version moves.

In the cloud they are rows scoped to the project: everyone on the team sees
the same thread, writing one needs the `write` permission, and another
project sees none of it.

## Watching a run happen

A recorded run is a file that has stopped growing; a running one is a file
that is still being appended to. The recorder writes a run's manifest when it
starts and flushes its events every quarter second, so a console in another
process can watch the directory and see the execution as it happens. The
agent does not know a console exists, and there is nothing extra to run.

Both streams are Server-Sent Events, and both end on their own: a run's
stream ends when the run does, and a quiet live stream lets go of its
connection rather than holding one of the browser's few open.

## Approving from the console

An agent asks for approval in its own process; the person answering is
looking at a console in another one. `InboxHandler` bridges them through the
same `.rewyn/` directory:

```python
from rewyn.human.approval import approval_scope, approve
from rewyn.human.inbox import InboxHandler


async def issue_refund(amount: int) -> str:
    with approval_scope(InboxHandler(timeout=300.0)):
        decision = await approve("issue_refund", amount=amount, risk="high")
    return "issued" if decision.approved else f"declined: {decision.reason}"
```

The request appears on the Live screen, the decision goes back to the waiting
agent, and both become events in the run. A request nobody answers is
rejected when it times out: an unattended agent fails closed.

## Reading a projection yourself

The read model is public API, so anything the console shows, a script can
compute:

```python
from rewyn.replay.recorder import RecordedRun
from rewyn.ui import projections

recorded = RecordedRun.load("latest")
context = projections.context(recorded)
for assembly in context.assemblies:
    print(assembly.used, "/", assembly.budget, assembly.by_kind)
```

## API reference

- `rewyn.ui.schemas` — the console wire format, shared by both surfaces.
  `CONSOLE_PREFIX` is where it is served: `/console/v1`.
- `rewyn.ui.projections` — `run_summary`, `run_detail`, `timeline`, `graph`,
  `context`, `model`, `prompt`, `memory`, `tools`, `mcp`, `skills`,
  `guardrails`, `approvals`, `all_projections`.
- `rewyn.ui.index` — `RunIndex`, `RunQuery`, the local runs index.
- `rewyn.ui.aggregates` — `overview`, `health`, `metrics`, `recent_changes`, `search`.
- `rewyn.ui.onboarding` — `document`, `steps`, `jobs`, `location`, `FIRST_RUN`.
- `rewyn.ui.demo` — `load`, `aload`, `clear`, `is_loaded`, `DEMO_TAG`.
- `rewyn.ui.incidents` — `detect`, `detail`, `timeline`, `incident_id`.
- `rewyn.ui.collaboration` — `CollabStore`, `parse_subject`, `subject_href`,
  `local_author`.
- `rewyn.ui.explain` — `explain`, `from_rules`, `ledger`, `ground`,
  `configured_model`.
- `rewyn.ui.lifecycle` — `workspace`, `loop`, `next_step`, `STAGES`, `LOOP`.
- `rewyn.ui.workspaces` — `plan_replay`, `replay_arguments`, `replay_view`,
  `diff_view`, `dataset_detail`; `ReplayJobs` tracks running replays.
- `rewyn.ui.registries` — `agent_summary`, `registry_entry`, `manifest_diff`,
  `evaluator_summary`, `report_summary`, `report_detail`, `experiment_plan`.
- `rewyn.ui.live` — `stream_run`, `stream_live`, `live_run`, `read_events_after`.
- `rewyn.ui.intelligence` — `dependency_map`, `drift_view`, `cost_view`,
  `release_view`, `release_id`, `refuse_promotion`, `promotion_record`,
  `experiment_view`, `notifications`.
- `rewyn.human.inbox` — `ApprovalInbox`, `InboxHandler`, `PendingApproval`.
- `rewyn.ui.server` — `build_app`, `serve`; the `rewyn ui` command.
- `rewyn_cloud.console` — the cloud implementation of the same routes.

## Failure modes

**`rewyn ui` says the console needs the 'ui' extra.** The server is
optional so that `pip install rewyn` stays small. Install
`rewyn[ui]`.

**The console API runs but the interface is not built.** The frontend is a
static bundle that has to be built once (`make build-web`). Until then the
page says so and the API still works: `/console/v1/docs` lists every endpoint.

**A run's panels are empty in the cloud but full locally.** Projections are
computed at ingest, so a run uploaded by an older build of the service has
none. Re-upload it (`rewyn sync --run <id>`) and they are rebuilt.

**A context item shows as withheld.** Items carry a sensitivity, and the
console filters those above the caller's role server-side. The item is
reported as withheld rather than silently dropped, so the reader knows the
context was larger than what they can see.

**The runs table is missing a run you just recorded.** The local index
refreshes at most once a second and keys off the manifest's modification
time; a run that is still open has no final manifest yet. It appears when it
finishes.

**A replay will not start.** A control is set to something this surface
cannot do -- `live` needs the agent's code. The replay workspace disables the
button and names the control; every other control still works, and a
reconstruction always does.

**A comparison offers nothing to compare against.** Comparison needs a second
run of the same agent. Replays of a run are excluded on purpose: the replay
workspace already shows a run against its own replay.

**A dataset case says "not run yet".** Cases are created in the console;
running them happens where your code is. The Regression screen composes the
command, and the report appears there afterwards.

**Drift says there is not enough history.** It compares two windows of runs,
so it needs at least two. This is deliberate: a trend drawn from one run is a
guess.

**A release is unverified.** No regression report names that agent. Run the
dataset against it and the release turns ready or blocked -- blocked names
the gate that failed. Promoting either one is refused: unverified is not the
same as safe, and the refusal says which of the two it is.

**Every run says `development`.** That is the default. `REWYN_ENV` sets the
environment for a process, and a run that passes
`metadata={"environment": ...}` overrides it -- the run knows better than the
shell does.

**The Live screen says nothing is running when something is.** Live mode
reads the same `.rewyn/` the console does, so an agent recording somewhere
else -- a different `REWYN_HOME`, another machine -- is invisible to it.
Sync those runs to the cloud console instead.

**An approval never appears.** Only `InboxHandler` publishes where the
console can see it; `ConsoleHandler` prompts the agent's own terminal and
`QueueHandler` keeps the request inside the agent's process.

**A navigation entry is dimmed with a dot beside it.** That screen has no
data to answer its question yet. The entry is never hidden — the sidebar is
the map of the product — and hovering it says what would unlock it: record a
run, save a run as a test, run a regression, or record two versions of an
agent.

**The console is full of an agent you have never written.** Something loaded
the demo project, or ran the examples. `rewyn demo clear` removes the demo;
the sidebar shows a `demo data` badge whenever it is loaded.

**A BUILD page is empty.** Nothing is registered up front: an entry appears
the first time a run uses it. Prompts in particular only appear when they are
recorded as versioned dependencies -- a run's exact prompt is always on its
Prompt tab regardless.

**An agent's version history has one row.** Versions come from the `version`
on the agent that ran. Bump it when you change the agent and the history, and
the version comparison, fill in.

**An incident disappeared.** An incident exists while its runs do. If the
failures stopped, or the runs aged past the window (24 hours locally, the
team's retention window in the cloud), it is no longer detected. What you
wrote on it -- the assignment, the status, the comments -- is kept, and comes
back if the same failure does.

**"Explain difference" says "composed by rule".** No explanation model is
configured. Set `REWYN_EXPLAIN_MODEL`; the rule-based answer uses the same
evidence and is the fallback whenever the agent cannot run or cites nothing.

**An explanation dropped a sentence.** The explanation agent wrote something
it could not tie to a difference in the comparison. Dropping it is the point:
a claim with no evidence is exactly what UI §23 forbids.

**The workspace says an agent was not found.** Agents appear once they have
run. Nothing is registered up front.

**A filter you set returns nothing.** Filters are applied server-side and
combine with AND. The facet endpoint (`/console/v1/runs/facets`) returns the
vocabulary that actually exists, with counts, which is the fastest way to see
what is filterable in this project.
