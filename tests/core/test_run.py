from __future__ import annotations

import asyncio

import pytest

from rewyn.core.event import EventType, ListSink
from rewyn.core.run import (
    DependencyRef,
    RunStatus,
    current_run,
    ensure_run,
    start_run,
)
from rewyn.core.span import SpanKind, current_span
from rewyn.core.sync import run_sync


def test_run_lifecycle_emits_start_and_finish() -> None:
    sink = ListSink()
    with start_run("demo", sinks=[sink]) as run:
        assert current_run() is run
        assert run.status is RunStatus.RUNNING
        run.emit(EventType.TOOL_CALLED, {"name": "x"})
    assert current_run() is None
    assert run.status is RunStatus.SUCCEEDED
    assert [e.type for e in sink.events] == [
        EventType.RUN_STARTED,
        EventType.TOOL_CALLED,
        EventType.RUN_FINISHED,
    ]
    assert [e.seq for e in sink.events] == [1, 2, 3]
    assert run.manifest.event_count == 3
    assert run.manifest.duration_ms is not None


def test_run_failure_is_recorded_and_reraised() -> None:
    sink = ListSink()
    with pytest.raises(ValueError, match="boom"), start_run("demo", sinks=[sink]) as run:
        raise ValueError("boom")
    assert run.status is RunStatus.FAILED
    failed = sink.of_type(EventType.RUN_FAILED)[0]
    assert failed.payload["error"] == "ValueError: boom"
    assert "Traceback" in failed.payload["traceback"]
    assert current_run() is None


def test_spans_nest_and_annotate_events() -> None:
    sink = ListSink()
    with start_run("demo", sinks=[sink]) as run:
        root = current_span()
        assert root is not None
        assert root.kind is SpanKind.RUN
        with run.span("outer", SpanKind.AGENT) as outer:
            assert outer.parent_id == root.id
            with run.span("inner", SpanKind.TOOL) as inner:
                event = run.emit(EventType.TOOL_CALLED, {})
                assert event.span_id == inner.id
                assert event.parent_span_id == outer.id
        assert current_span() is root
    assert all(s.ended_at is not None for s in run.spans)


def test_span_records_error() -> None:
    with start_run("demo", sinks=[]) as run, pytest.raises(RuntimeError), run.span("bad"):
        raise RuntimeError("nope")
    bad = next(s for s in run.spans if s.name == "bad")
    assert bad.status.value == "error"
    assert bad.error == "RuntimeError: nope"


def test_sink_failures_never_propagate() -> None:
    class Broken:
        def emit(self, event: object) -> None:
            raise OSError("disk full")

        def flush(self) -> None:
            raise OSError("disk full")

        def close(self) -> None:
            return None

    with start_run("demo", sinks=[Broken()]) as run:
        run.emit(EventType.MEMORY_READ, {})
    assert run.status is RunStatus.SUCCEEDED


def test_usage_cost_and_dependencies_accumulate() -> None:
    with start_run("demo", sinks=[]) as run:
        run.record_usage(input_tokens=10, output_tokens=5, model_calls=1)
        run.record_usage(input_tokens=1, tool_calls=2)
        run.record_cost("model", 0.25)
        run.add_dependency(DependencyRef(kind="model", name="fake", version="1"))
        run.add_dependency(DependencyRef(kind="model", name="fake", version="2"))
    assert run.manifest.usage.input_tokens == 11
    assert run.manifest.usage.total_tokens == 16
    assert run.manifest.usage.tool_calls == 2
    assert run.manifest.cost.total == 0.25
    assert [d.version for d in run.manifest.dependencies] == ["2"]


def test_ensure_run_reuses_active_run_or_creates_one() -> None:
    with start_run("outer", sinks=[]) as outer, ensure_run("inner") as inner:
        assert inner is outer
    with ensure_run("implicit") as implicit:
        assert implicit.name == "implicit"
        assert implicit.status is RunStatus.RUNNING
    assert implicit.status is RunStatus.SUCCEEDED


async def test_async_context_manager_and_contextvar_isolation() -> None:
    async with start_run("async", sinks=[]) as run:
        assert current_run() is run

        async def child() -> str:
            active = current_run()
            assert active is run
            return active.id

        assert await asyncio.gather(child(), child()) == [run.id, run.id]
    assert current_run() is None


def test_run_sync_outside_and_inside_event_loop() -> None:
    async def add(a: int, b: int) -> int:
        await asyncio.sleep(0)
        return a + b

    assert run_sync(add(1, 2)) == 3

    async def nested() -> int:
        return run_sync(add(2, 3))

    assert asyncio.run(nested()) == 5


def test_run_sync_propagates_context_and_errors() -> None:
    async def who() -> str | None:
        active = current_run()
        return active.id if active else None

    with start_run("ctx", sinks=[]) as run:
        assert run_sync(who()) == run.id

    async def fail() -> None:
        raise KeyError("missing")

    with pytest.raises(KeyError):
        run_sync(fail())
