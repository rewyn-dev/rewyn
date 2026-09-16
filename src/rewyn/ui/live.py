"""Live execution and its streams (UI spec §34, §53).

A recorded run is a file that stops growing; a running one is a file that is
still being appended to. That is the whole mechanism here: the agent's
recorder writes its manifest at run start and flushes events every quarter
second, so a console in another process can watch the directory and see an
execution as it happens -- without the agent knowing a console exists, and
without anything extra to run.

Frames are Server-Sent Events, which UI §53 permits and which survive proxies
that WebSockets do not. Both streams end on their own: a run's stream ends
when the run does, and the live stream ends when the client goes away.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from rewyn.core.event import Event, EventType
from rewyn.core.run import RunStatus
from rewyn.core.types import utcnow
from rewyn.replay.recorder import RecordedRun
from rewyn.storage.local import LocalStore, RunNotFoundError
from rewyn.ui import projections
from rewyn.ui import schemas as s

POLL_INTERVAL = 0.4
IDLE_LIMIT = 120.0
"""How long a stream stays open with nothing to say before it lets go.

A browser reconnects an ``EventSource`` on its own, so ending a quiet stream
costs the reader nothing and returns the connection -- which matters, because
a browser allows only a handful per host.
"""

# What the live view calls each stage of a run (UI §34).
STAGES: dict[EventType, str] = {
    EventType.RUN_STARTED: "starting",
    EventType.CONTEXT_ASSEMBLED: "building context",
    EventType.RETRIEVAL_QUERIED: "retrieving",
    EventType.MEMORY_READ: "reading memory",
    EventType.MODEL_CALLED: "waiting on the model",
    EventType.MODEL_RESPONSE: "thinking",
    EventType.TOOL_CALLED: "calling a tool",
    EventType.TOOL_RETURNED: "thinking",
    EventType.MCP_CONNECTED: "connecting to MCP",
    EventType.GRAPH_NODE_STARTED: "running a node",
    EventType.SUBAGENT_STARTED: "delegating",
    EventType.HUMAN_APPROVAL_REQUESTED: "waiting for approval",
    EventType.GUARDRAIL_TRIGGERED: "guardrail",
    EventType.RUN_FINISHED: "finished",
    EventType.RUN_FAILED: "failed",
}


def sse(payload: Any, *, event: str | None = None) -> str:
    """One Server-Sent Event frame."""
    body = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {body}\n\n"


def read_events_after(path: Path, after_seq: int) -> list[Event]:
    """Events past ``after_seq`` in an event log that may still be growing.

    A partially written final line is skipped rather than raising: the
    recorder appends whole lines, so the next poll picks it up complete.
    """
    if not path.exists():
        return []
    found: list[Event] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if int(record.get("seq", 0)) <= after_seq:
                continue
            try:
                found.append(Event.from_record(record))
            except ValueError:
                continue
    return found


def stage_of(events: list[Event]) -> str:
    for event in reversed(events):
        stage = STAGES.get(event.type)
        if stage:
            return stage
    return "running"


def live_run(store: LocalStore, run_id: str) -> s.LiveRun | None:
    """A snapshot of one in-flight run (UI §34)."""
    try:
        manifest = store.read_manifest(run_id)
        events = store.read_events(run_id)
    except (RunNotFoundError, OSError, ValueError):
        return None
    summary = projections.run_summary(manifest)
    assembled = [e for e in events if e.type is EventType.CONTEXT_ASSEMBLED]
    tokens = 0
    if assembled:
        decision = assembled[-1].payload.get("decision") or {}
        tokens = int(decision.get("used") or 0)
    graph = projections.graph(RecordedRun(manifest, events))
    return s.LiveRun(
        run=summary,
        events=len(events),
        stage=stage_of(events),
        context_tokens=tokens,
        cost=manifest.cost.total,
        nodes=graph.nodes[:12],
        updated_at=utcnow(),
    )


Disconnected = Callable[[], Awaitable[bool]]
"""Asked between frames: has the reader gone away?"""


async def _gone(disconnected: Disconnected | None) -> bool:
    """True when the client hung up.

    A stream that does not notice this holds its connection until it times
    out, and a browser only allows a few per host -- so the next page the
    reader opens waits on a socket nobody is reading.
    """
    return bool(disconnected is not None and await disconnected())


async def stream_run(
    store: LocalStore,
    run_id: str,
    *,
    after_seq: int = 0,
    interval: float = POLL_INTERVAL,
    idle_limit: float = IDLE_LIMIT,
    disconnected: Disconnected | None = None,
) -> AsyncIterator[str]:
    """Server-sent frames of one run, ending when the run does (UI §53)."""
    seq = after_seq
    waited = 0.0
    while not await _gone(disconnected):
        manifest = store.read_manifest(run_id)
        events = read_events_after(store.events_path(run_id), seq)
        finished = manifest.status not in (RunStatus.PENDING, RunStatus.RUNNING)
        if events:
            seq = events[-1].seq
            waited = 0.0
            recorded = RecordedRun(manifest, events)
            view = projections.timeline(recorded, limit=len(events))
            frame = s.StreamFrame(
                run_id=run_id,
                entries=view.entries,
                status=manifest.status.value,
                finished=finished,
                sent_at=utcnow(),
            )
            yield sse(frame.model_dump(mode="json"))
        if finished:
            yield sse(
                s.StreamFrame(
                    run_id=run_id,
                    status=manifest.status.value,
                    finished=True,
                    sent_at=utcnow(),
                ).model_dump(mode="json"),
                event="finished",
            )
            return
        await asyncio.sleep(interval)
        waited += interval
        if waited >= idle_limit:
            return


async def stream_live(
    store: LocalStore,
    running: Any,
    *,
    interval: float = POLL_INTERVAL,
    idle_limit: float = IDLE_LIMIT,
    disconnected: Disconnected | None = None,
) -> AsyncIterator[str]:
    """Server-sent frames of everything in flight (UI §34).

    ``running`` is a callable returning the run ids currently in flight, so
    the caller decides how to find them -- an index locally, a query in the
    cloud -- and this stays about the streaming.
    """
    waited = 0.0
    while waited < idle_limit and not await _gone(disconnected):
        ids = list(running())
        runs = [view for view in (live_run(store, run_id) for run_id in ids) if view is not None]
        yield sse(s.LiveFrame(runs=runs, sent_at=utcnow()).model_dump(mode="json"))
        await asyncio.sleep(interval)
        waited = 0.0 if runs else waited + interval
