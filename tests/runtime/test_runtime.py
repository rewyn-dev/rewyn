from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rewyn.core.event import EventType, ListSink
from rewyn.core.run import RunStatus, start_run
from rewyn.core.state import State
from rewyn.models.base import Message, StreamEvent
from rewyn.runtime import (
    CancellationToken,
    CancelledError,
    Checkpointer,
    CheckpointNotFoundError,
    EventStream,
    Executor,
    FileCheckpointStore,
    InMemoryCheckpointStore,
    SandboxSpec,
    SubprocessSandbox,
    cancellation_scope,
    check_cancelled,
    create,
    current_token,
    sandbox_tool,
)


async def test_checkpoint_save_restore_and_events() -> None:
    sink = ListSink()
    checkpointer = Checkpointer(InMemoryCheckpointStore())
    state = State({"n": 1})
    async with start_run("t", sinks=[sink]) as run:
        first = await checkpointer.save(
            state=state, messages=[Message.user("hi")], cursor={"iteration": 1}, label="one"
        )
        state["n"] = 2
        second = await checkpointer.save(state=state, cursor={"iteration": 2})
        restored = await checkpointer.restore(first.id)
        assert (await checkpointer.latest(run.id)) is not None
    assert restored.state.data == {"n": 1}
    assert restored.messages[0].text == "hi"
    assert restored.cursor == {"iteration": 1}
    assert second.sequence == 2
    assert restored.fingerprint() == first.fingerprint()
    created = sink.of_type(EventType.CHECKPOINT_CREATED)
    assert [e.payload["sequence"] for e in created] == [1, 2]
    assert created[0].payload["label"] == "one"
    assert sink.of_type(EventType.CHECKPOINT_RESTORED)[0].payload["checkpoint_id"] == first.id
    with pytest.raises(CheckpointNotFoundError):
        await checkpointer.restore("ckpt_missing")


async def test_file_checkpoint_store_persists_and_redacts(rewyn_home: Path) -> None:
    store = FileCheckpointStore()
    checkpointer = Checkpointer(store)
    async with start_run("t", sinks=[]) as run:
        saved = await checkpointer.save(
            state=State({"api_key": "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"}),
            messages=[Message.user("token sk-ant-api03-abcdefghijklmnopqrstuvwxyz")],
        )
    assert (rewyn_home / "checkpoints" / run.id / f"{saved.id}.json").exists()
    fresh = FileCheckpointStore()
    loaded = await fresh.load(saved.id)
    assert loaded is not None
    assert loaded.state.data["api_key"] == "[REDACTED]"
    assert "[REDACTED]" in loaded.messages[0].text
    assert [c.id for c in await fresh.list_for_run(run.id)] == [saved.id]
    assert await fresh.load("nope") is None


async def test_cancellation_token_and_executor() -> None:
    token = CancellationToken()
    seen: list[str | None] = []
    token.on_cancel(seen.append)
    with cancellation_scope(token):
        assert current_token() is token
        check_cancelled()
        token.cancel("user stop")
        with pytest.raises(CancelledError, match="user stop"):
            check_cancelled()
    assert seen == ["user stop"]
    assert current_token() is None

    async def slow() -> str:
        await asyncio.sleep(5)
        return "never"

    sink = ListSink()
    executor = Executor(timeout=0.05)
    async with start_run("t", sinks=[sink]) as run:
        with pytest.raises(CancelledError, match="timeout"):
            await executor.execute(slow)
        assert sink.of_type(EventType.RUN_CANCELLED)[0].payload["reason"] == "timeout"
    assert run.status is RunStatus.SUCCEEDED  # the error was handled inside the run

    async def fast() -> str:
        return "done"

    assert await Executor().execute(fast) == "done"


async def test_event_stream_yields_events_and_deltas() -> None:
    async with start_run("t", sinks=[]) as run:
        with EventStream(run, stop_on={EventType.AGENT_LOOP_FINISHED}) as stream:
            run.emit(EventType.AGENT_LOOP_STARTED, {})
            run.publish(StreamEvent(type="text_delta", text="hel"))
            run.emit(EventType.AGENT_LOOP_FINISHED, {})
            items = [item async for item in stream]
    assert [type(i).__name__ for i in items] == ["Event", "StreamEvent", "Event"]
    assert items[1].text == "hel"  # type: ignore[union-attr]


async def test_subprocess_sandbox_executes_and_records() -> None:
    sink = ListSink()
    sandbox = SubprocessSandbox(SandboxSpec(timeout=10))
    try:
        async with start_run("t", sinks=[sink]):
            ok = await sandbox.aexecute("print(2 + 2)")
            err = await sandbox.aexecute("import sys; sys.exit(3)")
            await sandbox.write_file("data/x.txt", "hello")
            shell = await sandbox.aexecute("cat data/x.txt", language="bash")
        assert ok.ok
        assert ok.stdout.strip() == "4"
        assert err.exit_code == 3
        assert shell.stdout == "hello"
        assert (await sandbox.read_file("data/x.txt")) == b"hello"
        assert (await sandbox.snapshot()) == {"data/x.txt": "hello"}
        with pytest.raises(Exception, match="escapes"):
            await sandbox.read_file("../outside")
        events = sink.of_type(EventType.SANDBOX_EXECUTED)
        assert [e.payload["exit_code"] for e in events] == [0, 3, 0]
        assert events[0].payload["code_hash"].startswith("sha256:")
    finally:
        await sandbox.aclose()


async def test_sandbox_timeout_and_tool() -> None:
    sandbox = SubprocessSandbox(SandboxSpec(timeout=0.2))
    try:
        result = await sandbox.aexecute("import time; time.sleep(5)")
        assert result.timed_out
        assert not result.ok
        tool = sandbox_tool(sandbox)
        out = await tool.ainvoke(code="print('hi')")
        assert out["stdout"] == "hi\n"
        assert tool.risk_level.value == "high"
    finally:
        await sandbox.aclose()


def test_sync_sandbox_create() -> None:
    sandbox = create(timeout=10)
    try:
        assert sandbox.name == "subprocess"
        result = sandbox.execute("print('sync')")  # type: ignore[attr-defined]
        assert result.stdout.strip() == "sync"
    finally:
        sandbox.close()  # type: ignore[attr-defined]
