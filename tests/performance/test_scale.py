"""Performance characteristics (spec §53, §55).

Spec §53 requires that instrumentation never becomes the bottleneck, and
§55 lists large context, long-running agents, many tool calls and large
graphs as required test categories.

These assert shape rather than wall-clock speed. A timing threshold on
shared CI is a flaky test; the things that actually break at scale are
quadratic growth, unbounded memory and a recorder that blocks its caller,
and each of those is checkable without a stopwatch.
"""

from __future__ import annotations

import time

import pytest

from rewyn import Agent, Graph, tool
from rewyn.context import Context, text_item
from rewyn.core.event import Event, EventType
from rewyn.core.run import start_run
from rewyn.graphs import END
from rewyn.runtime.recorder import Recorder
from rewyn.storage.local import LocalStore
from rewyn.testing import FakeModel


# Large context ------------------------------------------------------------------
def test_a_large_corpus_is_cut_to_the_budget():
    """Context assembly must bound the prompt, however much is offered."""
    context = Context(budget=1000)
    for index in range(2000):
        context.add(text_item(f"Document {index}. " * 20, source="corpus"))

    assembled = context.assemble_sync("anything")
    assert assembled.decision.used <= 1000
    assert len(assembled.decision.excluded) > 1500
    assert len(assembled.text()) < 20_000


def test_assembling_a_large_corpus_stays_linear():
    """Ten times the documents must not cost a hundred times the work."""

    def assemble(count: int) -> float:
        context = Context(budget=2000)
        for index in range(count):
            context.add(text_item(f"Document {index} about invoices.", source="corpus"))
        started = time.perf_counter()
        context.assemble_sync("invoices")
        return time.perf_counter() - started

    small = assemble(200)
    large = assemble(2000)
    assert large < max(small * 40, 2.0), "assembly is growing faster than linearly"


def test_the_budget_decision_explains_every_exclusion():
    context = Context(budget=200)
    for index in range(50):
        context.add(text_item(f"Document {index} " * 30, source="corpus"))
    decision = context.assemble_sync("q").decision
    assert all(item.reason for item in decision.excluded)


# Many tool calls ----------------------------------------------------------------
@tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


async def test_many_tool_calls_in_one_run():
    calls = [FakeModel.tool_calls(*[("add", {"a": i, "b": 1}) for i in range(20)])]
    calls.append("done")
    agent = Agent(FakeModel(calls), tools=[add], name="adder", max_iterations=4)

    async with start_run("many-tools", record=False) as run:
        result = await agent.arun("add lots")

    assert result.tool_calls == 20
    assert len(run.events_of(EventType.TOOL_RETURNED)) == 20
    assert run.manifest.usage.tool_calls == 20


async def test_tool_calls_in_one_turn_run_concurrently():
    """Twenty 50ms tools must not take a second."""
    import asyncio

    @tool
    async def slow(index: int) -> int:
        """Wait, then answer."""
        await asyncio.sleep(0.05)
        return index

    model = FakeModel([FakeModel.tool_calls(*[("slow", {"index": i}) for i in range(20)]), "done"])
    agent = Agent(model, tools=[slow], name="slow", max_iterations=3)

    started = time.perf_counter()
    await agent.arun("go")
    elapsed = time.perf_counter() - started
    assert elapsed < 0.6, f"tool calls appear to be serialised ({elapsed:.2f}s)"


# Long-running agents -------------------------------------------------------------
async def test_a_long_loop_is_bounded_by_its_budget():
    model = FakeModel([FakeModel.tool_call("add", {"a": 1, "b": 1})], cycle=True)
    agent = Agent(model, tools=[add], name="looper", max_iterations=50)
    result = await agent.arun("loop")
    assert result.iterations == 50
    assert result.stop_reason.value == "max_iterations"


async def test_a_long_run_does_not_grow_events_without_bound_when_not_kept():
    """A run that is not retaining events must not accumulate them in memory."""
    from rewyn.core.run import Run

    run = Run(name="streaming", keep_events=False)
    run.start()
    for index in range(5000):
        run.emit(EventType.STATE_UPDATED, {"i": index})
    run.finish()

    assert run.events == []
    assert run.manifest.event_count == 5002  # started, 5000 updates, finished


# Large graphs ---------------------------------------------------------------------
def wide_graph(width: int) -> Graph:
    graph = Graph("wide", max_steps=width * 4)
    graph.add_node("start", lambda s: {"seen": 0})
    for index in range(width):
        graph.add_node(f"worker_{index}", lambda s, i=index: {f"w{i}": i})
    graph.add_node("collect", lambda s: sum(v for k, v in s.items() if k.startswith("w")))
    graph.branch("start", lambda s: [f"worker_{i}" for i in range(width)])
    for index in range(width):
        graph.connect(f"worker_{index}", "collect")
    graph.connect("collect", END)
    return graph


async def test_a_wide_graph_fans_out_and_back_in():
    result = await wide_graph(40).arun({"input": "x"})
    assert result.ok
    assert result.output == sum(range(40))
    assert len([n for n in result.path if n.startswith("worker_")]) == 40


async def test_a_deep_graph_runs_every_node():
    depth = 100
    graph = Graph("deep", max_steps=depth * 2)
    graph.add_node("n0", lambda s: {"count": 1})
    for index in range(1, depth):
        graph.add_node(f"n{index}", lambda s: {"count": s["count"] + 1})
        graph.connect(f"n{index - 1}", f"n{index}")
    graph.connect(f"n{depth - 1}", END)

    result = await graph.arun({"input": "x"})
    assert result.state["count"] == depth
    assert result.steps == depth


async def test_a_runaway_graph_is_stopped_by_max_steps():
    """An unbounded cycle fails loudly rather than running forever."""
    from rewyn.graphs import GraphExecutionError

    graph = Graph("forever", max_steps=25)
    graph.add_node("loop", lambda s: {"n": s.get("n", 0) + 1})
    graph.connect("loop", "loop")
    with pytest.raises(GraphExecutionError, match="max_steps=25"):
        await graph.arun({"input": "x"})


# Recording under load --------------------------------------------------------------
def test_emitting_many_events_never_blocks_the_caller(rewyn_home):
    """The recorder is a queue put; it must stay fast regardless of the writer."""
    store = LocalStore(rewyn_home)
    store.initialize()
    recorder = Recorder(store, flush_interval=5.0)

    started = time.perf_counter()
    for seq in range(10_000):
        recorder.emit(Event(type=EventType.STATE_UPDATED, run_id="run_1", seq=seq))
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0, f"emitting 10k events took {elapsed:.2f}s"
    recorder.flush()
    recorder.close()


def test_the_recorder_sheds_load_rather_than_blocking(rewyn_home):
    """A full buffer drops and counts. Shedding is the fail-open choice."""
    recorder = Recorder(LocalStore(rewyn_home), max_buffer=10, flush_interval=60.0)

    started = time.perf_counter()
    for seq in range(2000):
        recorder.emit(Event(type=EventType.STATE_UPDATED, run_id="run_1", seq=seq))
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0, "emitting must never wait on the writer"
    recorder.close()


def test_a_recording_failure_never_reaches_the_caller(rewyn_home):
    class Broken(LocalStore):
        def append_events(self, run_id, events):
            raise OSError("disk is full")

    recorder = Recorder(Broken(rewyn_home), flush_interval=0.01)
    for seq in range(50):
        recorder.emit(Event(type=EventType.STATE_UPDATED, run_id="run_1", seq=seq))
    recorder.flush()
    recorder.close()
    assert recorder.failures > 0, "the failure was recorded"


@pytest.mark.parametrize("count", [100, 1000])
def test_reading_back_a_large_run_scales(rewyn_home, count):
    store = LocalStore(rewyn_home)
    store.initialize()
    store.append_events(
        "run_big",
        [Event(type=EventType.STATE_UPDATED, run_id="run_big", seq=i) for i in range(count)],
    )
    assert len(store.read_events("run_big")) == count
