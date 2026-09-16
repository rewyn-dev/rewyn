"""Live execution, its stream, and approving from the console (UI §34, §35, §53).

A running agent and a console are two processes sharing a directory, so these
tests do the same: they write a run the way the recorder does, and read it
back through the console while it is still being written.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from rewyn.core.event import Event, EventType
from rewyn.core.run import RunManifest, RunStatus
from rewyn.storage.local import LocalStore
from rewyn.ui import live
from rewyn.ui.server import PREFIX


def start_run_file(store: LocalStore, run_id: str = "run_live") -> RunManifest:
    """A run that has started and not finished, exactly as the recorder leaves it."""
    manifest = RunManifest(id=run_id, name="refund-agent", status=RunStatus.RUNNING)
    store.write_manifest(manifest)
    store.append_events(
        run_id, [Event(type=EventType.RUN_STARTED, run_id=run_id, seq=1, payload={"name": "x"})]
    )
    return manifest


def append(store: LocalStore, run_id: str, event_type: EventType, seq: int, **payload: object):
    store.append_events(run_id, [Event(type=event_type, run_id=run_id, seq=seq, payload=payload)])


def frames(text: str) -> list[dict]:
    """Parse a Server-Sent Events body into its payloads."""
    return [
        json.loads(line[len("data: ") :])
        for block in text.split("\n\n")
        for line in block.splitlines()
        if line.startswith("data: ")
    ]


def test_a_growing_event_log_is_read_without_its_partial_last_line(
    store: LocalStore, rewyn_home: Path
):
    """The recorder appends whole lines; a half-written one is picked up next poll."""
    start_run_file(store)
    append(store, "run_live", EventType.MODEL_CALLED, 2, model="gpt-x")
    path = store.events_path("run_live")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "TOOL_CALLED", "run_id": "run_live", "seq"')

    found = live.read_events_after(path, after_seq=1)
    assert [event.seq for event in found] == [2]

    with path.open("a", encoding="utf-8") as handle:
        handle.write(': 3, "id": "evt_3", "timestamp": "2026-09-13T10:00:00Z", "payload": {}}\n')
    assert [event.seq for event in live.read_events_after(path, after_seq=1)] == [2, 3]


def test_the_live_view_says_what_the_run_is_doing_right_now(store: LocalStore):
    """UI §34."""
    start_run_file(store)
    append(store, "run_live", EventType.MODEL_CALLED, 2, model="gpt-x")
    view = live.live_run(store, "run_live")
    assert view is not None
    assert view.stage == "waiting on the model"
    assert view.events == 2
    assert view.run.status == "running"

    append(store, "run_live", EventType.TOOL_CALLED, 3, name="lookup")
    assert live.live_run(store, "run_live").stage == "calling a tool"  # type: ignore[union-attr]

    append(store, "run_live", EventType.HUMAN_APPROVAL_REQUESTED, 4, action="refund")
    assert live.live_run(store, "run_live").stage == "waiting for approval"  # type: ignore[union-attr]


async def test_a_runs_stream_delivers_events_as_they_are_written(store: LocalStore):
    """UI §53: run started, tool called, tool returned, run completed."""
    start_run_file(store)
    stream = live.stream_run(store, "run_live", interval=0.02)

    first = await anext(stream)
    assert "RUN_STARTED" in first

    append(store, "run_live", EventType.TOOL_CALLED, 2, name="lookup")
    second = await anext(stream)
    assert "TOOL_CALLED" in second
    payload = frames(second)[0]
    assert payload["finished"] is False
    assert payload["entries"][0]["label"] == "Tool call"

    manifest = store.read_manifest("run_live")
    manifest.status = RunStatus.SUCCEEDED
    store.write_manifest(manifest)
    append(store, "run_live", EventType.RUN_FINISHED, 3, status="succeeded")

    rest = [chunk async for chunk in stream]
    assert any("RUN_FINISHED" in chunk for chunk in rest)
    assert frames(rest[-1])[0]["finished"] is True


async def test_the_live_stream_reports_what_is_in_flight(store: LocalStore):
    """UI §34."""
    start_run_file(store)
    stream = live.stream_live(store, lambda: ["run_live"], interval=0.02)
    frame = frames(await anext(stream))[0]
    assert [entry["run"]["id"] for entry in frame["runs"]] == ["run_live"]
    assert frame["runs"][0]["stage"] == "starting"


async def test_the_console_serves_a_snapshot_of_what_is_running(
    client: httpx.AsyncClient, store: LocalStore
):
    start_run_file(store)
    running = (await client.get(f"{PREFIX}/live/runs")).json()
    assert [entry["run"]["id"] for entry in running] == ["run_live"]
    assert running[0]["run"]["status"] == "running"


async def test_approving_from_the_console_releases_the_agent(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §35: the decision becomes part of the run."""
    from rewyn.core.run import start_run
    from rewyn.human.approval import approval_scope, approve
    from rewyn.human.inbox import ApprovalInbox, InboxHandler

    handler = InboxHandler(ApprovalInbox(store.home), timeout=5.0, poll_interval=0.02)

    async def decide_through_the_console() -> dict:
        for _ in range(200):
            pending = (await client.get(f"{PREFIX}/approvals", params={"pending": True})).json()
            if pending:
                response = await client.post(
                    f"{PREFIX}/approvals/{pending[0]['request_id']}/decision",
                    json={"approved": True, "by": "raj", "reason": "within policy"},
                )
                assert response.status_code == 200
                return response.json()
            await asyncio.sleep(0.02)
        pytest.fail("no approval request appeared")

    with approval_scope(handler):
        async with start_run("refund"):
            decision, view = await asyncio.gather(
                approve("issue_refund", amount=85_000, risk="high"),
                decide_through_the_console(),
            )

    assert decision.approved is True
    assert decision.by == "raj"
    assert view["decision"] == "approved"
    assert view["action"] == "issue_refund"
    assert view["details"] == {"amount": 85_000}

    listed = (await client.get(f"{PREFIX}/approvals")).json()
    assert listed[0]["decision"] == "approved"


async def test_deciding_twice_is_refused_with_an_explanation(
    client: httpx.AsyncClient, store: LocalStore
):
    from rewyn.human.approval import ApprovalRequest
    from rewyn.human.inbox import ApprovalInbox

    inbox = ApprovalInbox(store.home)
    request = ApprovalRequest(action="refund")
    inbox.submit(request)

    first = await client.post(f"{PREFIX}/approvals/{request.id}/decision", json={"approved": True})
    assert first.status_code == 200
    second = await client.post(
        f"{PREFIX}/approvals/{request.id}/decision", json={"approved": False}
    )
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "Approval unavailable"


async def test_an_unknown_approval_is_a_404(client: httpx.AsyncClient):
    response = await client.post(f"{PREFIX}/approvals/apr_nope/decision", json={"approved": True})
    assert response.status_code == 404


async def test_a_stream_stops_when_the_reader_goes_away(store: LocalStore):
    """A browser allows a handful of connections per host (UI §50, §53).

    A stream that keeps polling after its reader has gone holds one of them
    open, and the next page that reader opens waits behind it.
    """
    start_run_file(store)
    hung_up = False

    async def disconnected() -> bool:
        return hung_up

    stream = live.stream_run(store, "run_live", interval=0.01, disconnected=disconnected)
    assert "RUN_STARTED" in await anext(stream)

    hung_up = True
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_a_quiet_live_stream_lets_go(store: LocalStore):
    """Nothing is running: the stream ends rather than holding a socket open."""
    seen = [
        chunk async for chunk in live.stream_live(store, lambda: [], interval=0.01, idle_limit=0.05)
    ]
    assert seen, "it says 'nothing is running' before it lets go"
    assert all('"runs": []' in chunk for chunk in seen)
