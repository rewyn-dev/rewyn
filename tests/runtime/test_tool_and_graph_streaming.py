"""Tool and graph streaming (spec §41)."""

from __future__ import annotations

import pytest

from rewyn import Agent, Graph, tool
from rewyn.core.event import Event, EventType
from rewyn.core.run import start_run
from rewyn.graphs import END
from rewyn.models.base import StreamEvent
from rewyn.runtime.streaming import ToolProgress
from rewyn.testing import FakeModel


@tool
async def crawl(pages: int) -> str:
    """Crawl pages, reporting progress as it goes."""
    for index in range(pages):
        yield f"crawled page {index + 1}"
    yield f"done: {pages} pages"


@tool
def plain(x: str) -> str:
    """An ordinary tool."""
    return x.upper()


def test_a_generator_tool_is_recognised_as_streaming():
    assert crawl.is_streaming
    assert not plain.is_streaming


async def test_the_last_yield_is_the_result():
    assert await crawl.ainvoke(pages=2) == "done: 2 pages"


async def test_streaming_a_plain_tool_yields_one_value():
    assert [v async for v in plain.astream(x="hi")] == ["HI"]


async def test_progress_reaches_a_live_stream_and_the_result_reaches_the_model():
    agent = Agent(
        FakeModel([FakeModel.tool_call("crawl", {"pages": 3}), "finished"]),
        tools=[crawl],
        name="crawler",
    )
    progress: list[ToolProgress] = []
    final = None
    async for item in agent.astream("crawl 3 pages"):
        if isinstance(item, ToolProgress):
            progress.append(item)
        final = item

    assert [p.message for p in progress] == [
        "crawled page 1",
        "crawled page 2",
        "crawled page 3",
    ]
    assert [p.index for p in progress] == [0, 1, 2]
    assert all(p.name == "crawl" for p in progress)
    assert final.messages[2].tool_results[0].content == "done: 3 pages"


async def test_progress_is_counted_on_the_event_rather_than_flooding_the_log():
    """A tool reporting a hundred times must not add a hundred rows."""
    agent = Agent(
        FakeModel([FakeModel.tool_call("crawl", {"pages": 5}), "done"]),
        tools=[crawl],
        name="crawler",
    )
    async with start_run("counted", record=False) as run:
        await agent.arun("go")
    returned = run.events_of(EventType.TOOL_RETURNED)[0]
    assert returned.payload["progress_updates"] == 5
    assert len(run.events_of(EventType.TOOL_RETURNED)) == 1


async def test_dict_updates_are_carried_as_structured_data():
    @tool
    async def indexing(total: int) -> dict:
        """Report structured progress."""
        for done in range(1, total + 1):
            yield {"done": done, "total": total}
        yield {"status": "complete"}

    agent = Agent(
        FakeModel([FakeModel.tool_call("indexing", {"total": 2}), "ok"]),
        tools=[indexing],
        name="indexer",
    )
    updates = [i async for i in agent.astream("go") if isinstance(i, ToolProgress)]
    assert [u.data for u in updates] == [{"done": 1, "total": 2}, {"done": 2, "total": 2}]
    assert all(u.message == "" for u in updates)


async def test_a_streaming_tool_still_honours_its_timeout():
    import asyncio

    @tool(timeout=0.05)
    async def stalls() -> str:
        """Yield once then hang."""
        yield "started"
        await asyncio.sleep(5)
        yield "never"

    async with start_run("stalled", record=False) as run:
        agent = Agent(
            FakeModel([FakeModel.tool_call("stalls", {}), "done"]), tools=[stalls], name="s"
        )
        await agent.arun("go")
    returned = run.events_of(EventType.TOOL_RETURNED)[0]
    assert returned.payload["is_error"] is True
    assert "Timeout" in str(returned.payload["result"])


# Graph streaming ---------------------------------------------------------------
def pipeline() -> Graph:
    graph = Graph("pipeline")
    graph.add_node("research", lambda s: {"facts": ["a", "b"]})
    graph.add_node(
        "write",
        Agent(FakeModel(["report with 2 facts"]), name="writer", stream_model=True),
    )
    graph.connect("research", "write")
    graph.connect("write", END)
    return graph


async def test_a_graph_streams_its_nodes_and_finishes_with_the_result():
    nodes: list[str] = []
    deltas = 0
    final = None
    async for item in pipeline().astream({"input": "EV market"}):
        if isinstance(item, Event) and item.type is EventType.GRAPH_NODE_STARTED:
            nodes.append(str(item.payload["node"]))
        elif isinstance(item, StreamEvent) and item.type == "text_delta":
            deltas += 1
        final = item

    assert nodes == ["research", "write"]
    assert deltas > 0, "an agent node's tokens stream through the graph"
    assert final.output == "report with 2 facts"
    assert final.path == ["research", "write"]


async def test_graph_streaming_inside_an_existing_run_does_not_open_another():
    async with start_run("outer", record=False) as run:
        items = [i async for i in pipeline().astream({"input": "x"})]
    assert items[-1].ok
    assert run.status.value == "succeeded"


async def test_a_failing_graph_propagates_through_the_stream():
    graph = Graph("boom")

    def explode(_state):
        raise ValueError("node failed")

    graph.add_node("boom", explode)
    graph.connect("boom", END)

    with pytest.raises(Exception, match="node failed"):
        async for _ in graph.astream({"input": "x"}):
            pass
