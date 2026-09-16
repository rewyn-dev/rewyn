"""The seeded project every performance assertion runs against (UI spec §50).

The plan's U0 asks for one generator producing a project at the scale the
budgets are written for: 100,000 runs, a 5,000-event run, a 40-node graph and
a 2,400-case dataset. This is it.

Two seeding paths, because the budgets measure two different things:

``seed_index`` builds ``RunManifest`` objects in memory and writes them
through :meth:`RunIndex._write` -- the real indexing code path, so the rows
are exactly what real runs produce -- without touching the filesystem. The
list, facet and search budgets are about query cost at 100k cardinality, and
that is a SQLite question, not a filesystem one. Writing 100k manifest files
would measure the disk and take a minute to do it.

``seed_run`` writes a real manifest and event log through ``LocalStore``,
because the detail budgets (timeline, context, graph) are about reading and
projecting an actual recording. Those need only a handful of runs.

Nothing here is used by the product; it exists so that a budget can fail.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from rewyn.core.event import Event, EventType
from rewyn.core.run import DependencyRef, RunManifest, RunStatus
from rewyn.core.types import utcnow
from rewyn.storage.local import LocalStore
from rewyn.ui.index import RunIndex

AGENTS = ("refund-agent", "acme-credit", "onboarding-bot", "triage", "research")
MODELS = ("gpt-5", "claude-opus-5", "gemini-2.5-pro")
USERS = ("raj", "sam", "dana", "kim")
ENVIRONMENTS = ("production", "staging", "development")
TOOLS = ("search", "set_credit_limit", "lookup_account", "issue_refund")
SKILLS = ("refund-policy", "credit-policy", "tone")
ERRORS = (
    "ToolError: salesforce timeout",
    "ModelError: rate limited",
    "GuardrailError: pii detected",
)

# The plan's scale, in one place so a budget and its fixture cannot drift.
RUNS = 100_000
EVENTS_IN_A_BIG_RUN = 5_000
NODES_IN_A_BIG_GRAPH = 40
CASES_IN_A_BIG_DATASET = 2_400
CONTEXT_ITEMS = 200


@dataclass(frozen=True)
class SeededProject:
    """What a seeded project left behind, so a test can address it."""

    runs: int
    big_run_id: str | None = None
    graph_run_id: str | None = None
    dataset: str | None = None


def _manifest(index: int, *, now: datetime, rng: random.Random) -> RunManifest:
    """One synthetic run, shaped like a real one."""
    agent = AGENTS[index % len(AGENTS)]
    failed = index % 17 == 0
    started = now - timedelta(seconds=index * 3)
    manifest = RunManifest(
        id=f"run_seed{index:08d}",
        name=agent,
        project="default",
        status=RunStatus.FAILED if failed else RunStatus.SUCCEEDED,
        started_at=started,
        ended_at=started + timedelta(milliseconds=800 + (index % 4000)),
        tags=["seeded"],
        metadata={
            "environment": ENVIRONMENTS[index % len(ENVIRONMENTS)],
            "user": USERS[index % len(USERS)],
            "session_id": f"sess-{index // 7}",
        },
        error=ERRORS[index % len(ERRORS)] if failed else None,
    )
    manifest.cost.model = round(0.002 + (index % 40) / 1000.0, 5)
    manifest.dependencies = [
        DependencyRef(kind="agent", name=agent, version=str(1 + index % 4)),
        DependencyRef(kind="model", name=MODELS[index % len(MODELS)], version="1"),
        DependencyRef(kind="tool", name=TOOLS[index % len(TOOLS)], version="1"),
        DependencyRef(kind="skill", name=SKILLS[index % len(SKILLS)], version=str(17 + index % 3)),
    ]
    return manifest


def seed_index(index: RunIndex, count: int = RUNS, *, now: datetime | None = None) -> int:
    """Put ``count`` synthetic runs into the index, in one transaction.

    Returns the number written. This bypasses the filesystem on purpose (see
    the module docstring); it uses the index's own writer, so every column,
    facet, tag and search-text row is produced by the code that serves real
    runs.
    """
    moment = now or utcnow()
    rng = random.Random(17)
    # The fixture writes through the index's own writer on purpose: the rows
    # must be the ones real runs produce, or the budget measures a fiction.
    with index._connect() as connection:
        connection.execute("PRAGMA synchronous = OFF")
        for position in range(count):
            manifest = _manifest(position, now=moment, rng=rng)
            index._write(connection, manifest, float(position))
    return count


def big_run(event_count: int = EVENTS_IN_A_BIG_RUN) -> tuple[RunManifest, list[Event]]:
    """A single run with a long event log and a wide context assembly."""
    started = utcnow() - timedelta(minutes=10)
    manifest = RunManifest(
        id="run_seedbig",
        name="acme-credit",
        project="default",
        status=RunStatus.SUCCEEDED,
        started_at=started,
        ended_at=started + timedelta(seconds=40),
        metadata={"environment": "production", "user": "raj"},
    )
    manifest.dependencies = [
        DependencyRef(kind="agent", name="acme-credit", version="3"),
        DependencyRef(kind="model", name="gpt-5", version="1"),
    ]
    events: list[Event] = [
        Event(
            type=EventType.CONTEXT_ASSEMBLED,
            run_id=manifest.id,
            seq=1,
            payload={
                "name": "acme-context",
                "decision": {
                    "used": 7_800,
                    "budget": 8_000,
                    "by_kind": {"document": 5_000, "memory": 1_800, "skill": 1_000},
                    "included": [
                        {
                            "id": f"doc-{item}",
                            "kind": "document",
                            "tokens": 39,
                            "source": f"kb://policy/{item}",
                            "version": "19",
                            "relevance": 0.9 - item / 1000.0,
                            "trust_level": "trusted",
                        }
                        for item in range(CONTEXT_ITEMS)
                    ],
                    "excluded": [],
                },
            },
        )
    ]
    for seq in range(2, event_count + 1):
        events.append(
            Event(
                type=EventType.TOOL_CALLED if seq % 2 else EventType.TOOL_RETURNED,
                run_id=manifest.id,
                seq=seq,
                payload={"name": TOOLS[seq % len(TOOLS)], "arguments": {"index": seq}},
            )
        )
    manifest.event_count = len(events)
    return manifest, events


def graph_run(nodes: int = NODES_IN_A_BIG_GRAPH) -> tuple[RunManifest, list[Event]]:
    """A run whose execution graph has ``nodes`` nodes."""
    started = utcnow() - timedelta(minutes=5)
    manifest = RunManifest(
        id="run_seedgraph",
        name="wide-graph",
        project="default",
        status=RunStatus.SUCCEEDED,
        started_at=started,
        ended_at=started + timedelta(seconds=12),
        metadata={"environment": "production"},
    )
    manifest.dependencies = [DependencyRef(kind="graph", name="wide-graph", version="1")]
    events: list[Event] = [
        Event(
            type=EventType.GRAPH_STARTED,
            run_id=manifest.id,
            seq=1,
            payload={"name": "wide-graph", "nodes": nodes},
        )
    ]
    seq = 2
    for node in range(nodes):
        events.append(
            Event(
                type=EventType.GRAPH_NODE_STARTED,
                run_id=manifest.id,
                seq=seq,
                payload={"node": f"node-{node}", "graph": "wide-graph"},
            )
        )
        seq += 1
        events.append(
            Event(
                type=EventType.GRAPH_NODE_FINISHED,
                run_id=manifest.id,
                seq=seq,
                payload={
                    "node": f"node-{node}",
                    "graph": "wide-graph",
                    "next": [f"node-{node + 1}"] if node + 1 < nodes else [],
                },
            )
        )
        seq += 1
    events.append(
        Event(
            type=EventType.GRAPH_FINISHED,
            run_id=manifest.id,
            seq=seq,
            payload={"name": "wide-graph"},
        )
    )
    manifest.event_count = len(events)
    return manifest, events


def seed_run(store: LocalStore, manifest: RunManifest, events: list[Event]) -> str:
    """Write one real recording through the store, so detail endpoints can read it."""
    store.initialize()
    store.write_manifest(manifest)
    store.append_events(manifest.id, events)
    return manifest.id


def seed_dataset(store: LocalStore, cases: int = CASES_IN_A_BIG_DATASET) -> str:
    """A dataset at the scale §50's dataset budget is written for."""
    from rewyn.evaluation.dataset import Dataset

    dataset = Dataset(name="seeded-regression")
    for case in range(cases):
        dataset.add(f"case {case}", "done")
    dataset.save(home=store.home)
    return dataset.name


def seed_project(
    store: LocalStore,
    index: RunIndex,
    *,
    runs: int = RUNS,
    with_details: bool = True,
) -> SeededProject:
    """The whole seeded project the plan's U0 asks for.

    The real recordings are written and indexed *first*, because
    :meth:`RunIndex.refresh` removes rows whose run directory is gone -- which
    is every synthetic row. Seed the files, index them, then bulk-load, and
    leave refresh alone from then on.
    """
    if with_details:
        big_manifest, big_events = big_run()
        graph_manifest, graph_events = graph_run()
        big_id = seed_run(store, big_manifest, big_events)
        graph_id = seed_run(store, graph_manifest, graph_events)
        dataset = seed_dataset(store)
        index.refresh(force=True)
    else:
        big_id = graph_id = dataset = None
    seed_index(index, runs)
    return SeededProject(runs=runs, big_run_id=big_id, graph_run_id=graph_id, dataset=dataset)
