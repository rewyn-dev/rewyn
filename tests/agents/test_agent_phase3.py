from __future__ import annotations

import pytest

from rewyn import Agent, tool
from rewyn.agents import (
    LoggingHooks,
    LoopContext,
    RunResult,
    StopReason,
    evaluator_stop,
    handoff,
    known_strategies,
    run_subagent,
)
from rewyn.core.event import Event, EventType, ListSink
from rewyn.core.run import start_run
from rewyn.models.base import StreamEvent
from rewyn.runtime import Checkpointer, InMemoryCheckpointStore
from rewyn.testing import FakeModel


@tool
def lookup(topic: str) -> str:
    """Look something up."""
    return f"facts about {topic}"


def test_strategies_are_registered() -> None:
    assert {"react", "plan_execute", "reflection"} <= set(known_strategies())


async def test_plan_execute_strategy() -> None:
    model = FakeModel(
        [
            '{"goal": "report", "steps": ["gather", "write"]}',
            FakeModel.tool_call("lookup", {"topic": "ev"}),
            "gathered",
            "written",
            "Final report.",
        ]
    )
    sink = ListSink()
    agent = Agent(model=model, tools=[lookup], loop="plan_execute", max_iterations=10)
    async with start_run("t", sinks=[sink]):
        result = await agent.arun("Write an EV report")
    assert result.ok
    assert result.output == "Final report."
    plan = sink.of_type(EventType.PLAN_CREATED)[0]
    assert plan.payload["steps"] == ["gather", "write"]
    assert result.iterations == 4  # plan, step 1, step 2, finish
    assert "Execute step 1 of 2: gather" in model.requests[1].messages[-1].text


async def test_reflection_strategy_revises_once() -> None:
    model = FakeModel(
        [
            "Draft answer",
            '{"approved": false, "feedback": "add numbers"}',
            "Better answer with 42",
            '{"approved": true}',
        ]
    )
    agent = Agent(model=model, loop="reflection", max_iterations=5)
    result = await agent.arun("question")
    assert result.output == "Better answer with 42"
    assert "add numbers" in model.requests[2].messages[-1].text
    assert result.iterations == 2


async def test_subagents_delegate_inside_parent_run() -> None:
    researcher = Agent(model=FakeModel(["EV share is 24%."], cycle=True), name="researcher")
    parent_model = FakeModel(
        [FakeModel.tool_call("delegate_to_researcher", {"task": "EV share?"}), "Answer: 24%."]
    )
    sink = ListSink()
    parent = Agent(model=parent_model, name="lead", subagents=[researcher])
    async with start_run("t", sinks=[sink]) as run:
        result = await parent.arun("What is EV share?")
    assert result.output == "Answer: 24%."
    assert result.messages[2].tool_results[0].content == "EV share is 24%."
    started = sink.of_type(EventType.SUBAGENT_STARTED)[0]
    assert started.payload["subagent"] == "researcher"
    assert started.payload["parent"] == "lead"
    finished = sink.of_type(EventType.SUBAGENT_FINISHED)[0]
    assert finished.payload["stop_reason"] == "completed"
    assert len(sink.of_type(EventType.AGENT_LOOP_STARTED)) == 2
    sub_span = next(s for s in run.spans if s.name == "subagent:researcher")
    inner_agent_span = next(s for s in run.spans if s.name == "agent:researcher")
    assert inner_agent_span.parent_id == sub_span.id
    assert (await run_subagent(researcher, "again")).output == "EV share is 24%."


async def test_handoff_transfers_conversation() -> None:
    billing = Agent(model=FakeModel(["Refund processed by billing."]), name="billing")
    triage_model = FakeModel(
        [
            FakeModel.tool_call(
                "handoff_to_billing", {"reason": "refund", "summary": "wants money back"}
            )
        ]
    )
    sink = ListSink()
    triage = Agent(model=triage_model, name="triage", handoffs=[billing])
    async with start_run("t", sinks=[sink]):
        result = await triage.arun("I want a refund")
    assert result.stop_reason is StopReason.HANDOFF
    assert result.output == "Refund processed by billing."
    record = sink.of_type(EventType.HANDOFF)[0].payload
    assert record["sender"] == "triage"
    assert record["receiver"] == "billing"
    assert record["reason"] == "refund"
    assert record["messages_transferred"] == 2  # user + assistant tool call
    billing_input = billing.model.requests[0].messages  # type: ignore[attr-defined]
    assert billing_input[0].text == "I want a refund"
    assert "Summary: wants money back" in billing_input[-1].text

    direct = await handoff("human", billing, reason="escalation")
    assert isinstance(direct, RunResult)


async def test_lifecycle_hooks_and_evaluator_stop() -> None:
    hooks = LoggingHooks()
    model = FakeModel([FakeModel.tool_call("lookup", {"topic": "x"}), "done"])
    agent = Agent(model=model, tools=[lookup], hooks=[hooks])
    await agent.arun("go")
    assert hooks.lines == [
        "start agent",
        "model tool_calls",
        "tools 1",
        "iteration 1 done=False",
        "model stop",
        "iteration 2 done=True",
        "finish completed",
    ]

    def good_enough(ctx: LoopContext) -> bool:
        return ctx.iteration >= 1

    stopper = Agent(
        model=FakeModel([FakeModel.tool_call("lookup", {"topic": "x"})], cycle=True),
        tools=[lookup],
        stop_when=evaluator_stop(good_enough),
    )
    assert (await stopper.arun("loop")).stop_reason is StopReason.EVALUATOR

    failing = Agent(model=FakeModel([RuntimeError("down")]), hooks=[hooks])  # type: ignore[list-item]
    with pytest.raises(RuntimeError):
        await failing.arun("x")
    assert hooks.lines[-1] == "error RuntimeError"


async def test_agent_checkpoints_and_resume() -> None:
    checkpointer = Checkpointer(InMemoryCheckpointStore())
    model = FakeModel([FakeModel.tool_call("lookup", {"topic": "a"}), "final"])
    agent = Agent(model=model, tools=[lookup], checkpointer=checkpointer)
    sink = ListSink()
    async with start_run("t", sinks=[sink]) as run:
        result = await agent.arun("start", state={"k": "v"})
        checkpoints = await checkpointer.store.list_for_run(run.id)
    assert result.ok
    assert [c.cursor["iteration"] for c in checkpoints] == [1, 2]
    first = checkpoints[0]
    assert len(first.messages) == 3  # user, assistant tool call, tool result
    resumed_model = FakeModel(["resumed answer"])
    resumed = Agent(model=resumed_model, tools=[lookup], checkpointer=checkpointer)
    out = await resumed.arun("ignored", resume_from=first.id)
    assert out.output == "resumed answer"
    assert out.iterations == 2
    assert out.state == {"k": "v"}
    assert resumed_model.requests[0].messages[-1].tool_results[0].content == "facts about a"


async def test_astream_yields_events_deltas_and_result() -> None:
    model = FakeModel(
        [FakeModel.tool_call("lookup", {"topic": "z"}), "streamed answer"], chunk_size=5
    )
    agent = Agent(model=model, tools=[lookup])
    items = [item async for item in agent.astream("go")]
    assert isinstance(items[-1], RunResult)
    assert items[-1].output == "streamed answer"
    deltas = [i.text for i in items if isinstance(i, StreamEvent) and i.type == "text_delta"]
    assert "".join(deltas) == "streamed answer"
    events = [i.type for i in items if isinstance(i, Event)]
    assert events[0] is EventType.RUN_STARTED
    assert EventType.TOOL_CALLED in events
    assert events[-1] is EventType.AGENT_LOOP_FINISHED
    assert not agent.stream_model
