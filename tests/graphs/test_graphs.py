from __future__ import annotations

import asyncio

import pytest

from rewyn import Agent, Graph
from rewyn.core.event import EventType, ListSink
from rewyn.core.run import start_run
from rewyn.core.state import State
from rewyn.core.types import ConfigurationError
from rewyn.graphs import END, GraphExecutionError, NodeContext
from rewyn.human import AutoApprove, AutoReject
from rewyn.runtime import Checkpointer, InMemoryCheckpointStore
from rewyn.testing import FakeModel


def _pipeline() -> Graph:
    graph = Graph("pipeline")
    graph.add_node("research", lambda s: {"facts": ["a", "b"]})

    async def analyze(state: State) -> dict[str, int]:
        await asyncio.sleep(0)
        return {"count": len(state["facts"])}

    graph.add_node("analyze", analyze)
    graph.add_node("write", lambda s: f"report with {s['count']} facts", output_key="report")
    graph.connect("research", "analyze")
    graph.connect("analyze", "write")
    graph.connect("write", END)
    return graph


async def test_sequential_graph_emits_events_and_tracks_state() -> None:
    sink = ListSink()
    graph = _pipeline()
    async with start_run("t", sinks=[sink]) as run:
        result = await graph.arun({"input": "EV market"})
    assert result.ok
    assert result.path == ["research", "analyze", "write"]
    assert result.output == "report with 2 facts"
    assert result.state["report"] == "report with 2 facts"
    assert result.steps == 3
    types = [e.type for e in sink.events]
    assert types.count(EventType.GRAPH_NODE_STARTED) == 3
    assert types.count(EventType.STATE_UPDATED) == 3
    finished = sink.of_type(EventType.GRAPH_FINISHED)[0]
    assert finished.payload["path"] == result.path
    assert finished.payload["output"] == result.output
    assert sink.of_type(EventType.GRAPH_STARTED)[0].payload["entry"] == "research"
    assert run.manifest.dependencies[0].kind == "graph"
    node_spans = [s.name for s in run.spans if s.kind.value == "node"]
    assert node_spans == ["node:research", "node:analyze", "node:write"]
    assert graph.fingerprint() == _pipeline().fingerprint()


def test_sync_run_with_string_input_and_validation() -> None:
    graph = Graph("g")
    graph.add_node("echo", lambda s: s["input"].upper())
    assert graph.run("hello").output == "HELLO"
    with pytest.raises(ConfigurationError, match="unknown node"):
        graph.connect("echo", "missing")
    with pytest.raises(ConfigurationError, match="already exists"):
        graph.add_node("echo", lambda s: None)
    with pytest.raises(TypeError, match="callable"):
        graph.add_node("bad", 42)


async def test_conditional_edges_router_and_loops() -> None:
    graph = Graph("loop", max_steps=20)
    graph.add_node("start", lambda s: {"n": s.get("n", 0)})
    graph.add_node("increment", lambda s: {"n": s["n"] + 1})
    graph.add_node("done", lambda s: f"finished at {s['n']}", output_key="final")
    graph.connect("start", "increment")
    graph.connect("increment", "increment", when=lambda s: s["n"] < 3)
    graph.connect("increment", "done", when=lambda s: s["n"] >= 3)
    result = await graph.arun({"n": 0})
    assert result.state["n"] == 3
    assert result.path == ["start", "increment", "increment", "increment", "done"]

    router = Graph("router")
    router.add_node("classify", lambda s: {"kind": "refund" if "refund" in s["input"] else "other"})
    router.add_node("refund", lambda s: "refund flow", output_key="handled")
    router.add_node("other", lambda s: "generic flow", output_key="handled")
    router.branch("classify", lambda s: s["kind"])
    assert (await router.arun("please refund me")).output == "refund flow"
    assert (await router.arun("hello")).output == "generic flow"
    router.branch("classify", lambda s: "nowhere")
    with pytest.raises(GraphExecutionError, match="unknown node"):
        await router.arun("x")


async def test_infinite_loop_is_bounded() -> None:
    graph = Graph("forever", max_steps=5)
    graph.add_node("a", lambda s: None)
    graph.connect("a", "a")
    sink = ListSink()
    with pytest.raises(GraphExecutionError, match="max_steps"):
        async with start_run("t", sinks=[sink]):
            await graph.arun({})
    assert sink.of_type(EventType.GRAPH_FINISHED)[0].payload["status"] == "failed"


async def test_parallel_fan_out_and_fan_in() -> None:
    order: list[str] = []

    async def slow(name: str, delay: float) -> dict[str, str]:
        await asyncio.sleep(delay)
        order.append(name)
        return {name: "ok"}

    graph = Graph("parallel")
    graph.add_node("start", lambda s: None)
    graph.add_node("left", lambda s: slow("left", 0.05))
    graph.add_node("right", lambda s: slow("right", 0.0))
    graph.add_node("join", lambda s: f"{s['left']}+{s['right']}", output_key="joined")
    graph.connect("start", "left")
    graph.connect("start", "right")
    graph.connect("left", "join")
    graph.connect("right", "join")
    result = await graph.arun({})
    assert order == ["right", "left"]  # ran concurrently
    assert result.path == ["start", "left", "right", "join"]
    assert result.output == "ok+ok"
    assert result.path.count("join") == 1


async def test_retries_timeouts_and_node_context() -> None:
    attempts = {"n": 0}

    def flaky(ctx: NodeContext) -> dict[str, int]:
        attempts["n"] += 1
        if ctx.attempt < 3:
            raise RuntimeError("flaky")
        return {"attempts": ctx.attempt, "step": ctx.step}

    sink = ListSink()
    graph = Graph("retry")
    graph.add_node("flaky", flaky, retries=3, retry_delay=0.001)
    async with start_run("t", sinks=[sink]):
        result = await graph.arun({})
    assert result.state["attempts"] == 3
    failed = sink.of_type(EventType.GRAPH_NODE_FAILED)
    assert [e.payload["will_retry"] for e in failed] == [True, True]

    async def hang(state: State) -> None:
        await asyncio.sleep(2)

    slow = Graph("slow")
    slow.add_node("hang", hang, timeout=0.05)
    with pytest.raises(TimeoutError):
        await slow.arun({})


async def test_approval_gate_and_conditional_routing() -> None:
    sink = ListSink()
    graph = Graph("approval")
    graph.add_node("propose", lambda s: {"amount": 85000})
    graph.add_node("issue", lambda s: "refund issued", approval=True, output_key="result")
    graph.add_node("decline", lambda s: "refund declined", output_key="result")
    graph.connect("propose", "issue")
    graph.connect("issue", "decline", when=lambda s: s.get("issue.approved") is False)
    async with start_run("t", sinks=[sink]):
        approved = await graph.arun({}, approval_handler=AutoApprove())
        rejected = await graph.arun({}, approval_handler=AutoReject("policy"))
    assert approved.output == "refund issued"
    assert rejected.output == "refund declined"
    assert rejected.state["issue.approved"] is False
    assert len(sink.of_type(EventType.HUMAN_APPROVAL_REQUESTED)) == 2
    skipped = [e for e in sink.of_type(EventType.GRAPH_NODE_FINISHED) if e.payload.get("skipped")]
    assert skipped[0].payload["reason"] == "approval rejected: policy"


async def test_checkpoints_and_resume() -> None:
    calls: list[str] = []
    graph = Graph("resumable")
    graph.add_node("one", lambda s: calls.append("one") or {"a": 1})
    graph.add_node("two", lambda s: calls.append("two") or {"b": 2})
    graph.add_node(
        "three", lambda s: calls.append("three") or f"{s['a']}{s['b']}", output_key="out"
    )
    graph.connect("one", "two")
    graph.connect("two", "three")
    checkpointer = Checkpointer(InMemoryCheckpointStore())
    sink = ListSink()
    async with start_run("t", sinks=[sink]) as run:
        result = await graph.arun({}, checkpointer=checkpointer)
        saved = await checkpointer.store.list_for_run(run.id)
    assert result.output == "12"
    assert len(saved) == 3
    assert result.checkpoint_id == saved[-1].id
    after_one = saved[0]
    assert after_one.cursor["frontier"] == ["two"]
    calls.clear()
    resumed = await graph.arun({}, checkpointer=checkpointer, resume_from=after_one.id)
    assert calls == ["two", "three"]
    assert resumed.output == "12"
    assert resumed.path == ["one", "two", "three"]


async def test_agent_and_subgraph_nodes() -> None:
    writer = Agent(model=FakeModel(["A fine report."]), name="writer")
    inner = Graph("inner")
    inner.add_node("shout", lambda s: {"loud": s["input"].upper()})
    outer = Graph("outer")
    outer.add_node("prep", lambda s: {"input": "draft a report"})
    outer.add_node("write", writer)
    outer.add_subgraph("inner", inner)
    outer.connect("prep", "write")
    outer.connect("write", "inner")
    sink = ListSink()
    async with start_run("t", sinks=[sink]):
        result = await outer.arun({})
    assert result.state["write"] == "A fine report."
    assert result.state["loud"] == "DRAFT A REPORT"
    kinds = [e.payload["kind"] for e in sink.of_type(EventType.GRAPH_NODE_STARTED)]
    assert kinds == ["function", "agent", "subgraph", "function"]
    assert len(sink.of_type(EventType.AGENT_LOOP_STARTED)) == 1
