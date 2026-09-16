from __future__ import annotations

import pytest
from pydantic import BaseModel

from rewyn import Agent, tool
from rewyn.agents import Loop, LoopContext, StopReason
from rewyn.core.event import EventType, ListSink
from rewyn.core.run import RunStatus, start_run
from rewyn.core.types import ConfigurationError
from rewyn.models.base import StructuredOutputError
from rewyn.storage.local import LocalStore
from rewyn.testing import FakeModel


@tool
def lookup(city: str) -> str:
    """Look up the weather."""
    return f"sunny in {city}"


def test_react_loop_calls_tools_until_final_answer(rewyn_home: object) -> None:
    model = FakeModel(
        [
            FakeModel.tool_call("lookup", {"city": "Paris"}, text="checking"),
            "It is sunny in Paris.",
        ]
    )
    agent = Agent(model=model, name="weather", instructions="Be helpful.", tools=[lookup])
    result = agent.run("Weather in Paris?")
    assert result.ok
    assert result.output == "It is sunny in Paris."
    assert result.iterations == 2
    assert result.tool_calls == 1
    assert result.usage.total_tokens > 0
    assert result.cost > 0
    roles = [m.role.value for m in result.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert result.messages[3].tool_results[0].content == "sunny in Paris"
    # The second model call saw the tool result.
    assert model.requests[1].messages[3].tool_results[0].content == "sunny in Paris"
    assert model.requests[0].tools[0].name == "lookup"

    store = LocalStore()
    from rewyn.runtime.recorder import default_recorder

    default_recorder().flush()
    manifest = store.read_manifest(result.run_id)
    assert manifest.status is RunStatus.SUCCEEDED
    assert manifest.output == "It is sunny in Paris."
    assert manifest.usage.model_calls == 2
    assert manifest.usage.tool_calls == 1
    kinds = {d.kind for d in manifest.dependencies}
    assert kinds == {"agent", "model", "tool"}
    types = [e.type for e in store.read_events(result.run_id)]
    assert types[:3] == [
        EventType.RUN_STARTED,
        EventType.AGENT_LOOP_STARTED,
        EventType.MODEL_CALLED,
    ]
    assert types.count(EventType.AGENT_LOOP_ITERATION) == 2
    assert EventType.AGENT_LOOP_FINISHED in types
    assert types[-1] is EventType.RUN_FINISHED


def test_agent_reuses_active_run_and_emits_events() -> None:
    sink = ListSink()
    agent = Agent(model=FakeModel(["done"]), name="inner")
    with start_run("outer", sinks=[sink]) as run:
        result = agent.run("hi")
    assert result.run_id == run.id
    started = sink.of_type(EventType.AGENT_LOOP_STARTED)[0]
    assert started.payload["strategy"] == "react"
    assert started.payload["budget"]["max_iterations"] == 10
    finished = sink.of_type(EventType.AGENT_LOOP_FINISHED)[0]
    assert finished.payload["stop_reason"] == "completed"
    agent_span = next(s for s in run.spans if s.name == "agent:inner")
    iteration_span = next(s for s in run.spans if s.name == "iteration:1")
    assert iteration_span.parent_id == agent_span.id


def test_budgets_stop_the_loop() -> None:
    endless = FakeModel([FakeModel.tool_call("lookup", {"city": "x"})], cycle=True)
    agent = Agent(model=endless, tools=[lookup], max_iterations=3)
    result = agent.run("loop forever")
    assert result.stop_reason is StopReason.MAX_ITERATIONS
    assert result.iterations == 3
    assert not result.ok

    costly = FakeModel([FakeModel.tool_call("lookup", {"city": "x"})], cycle=True)
    agent = Agent(model=costly, tools=[lookup], loop=Loop(max_iterations=50, max_cost=0.0000001))
    assert agent.run("x").stop_reason is StopReason.MAX_COST

    tokens = FakeModel([FakeModel.tool_call("lookup", {"city": "x"})], cycle=True)
    agent = Agent(model=tokens, tools=[lookup], max_iterations=50, max_tokens=5)
    assert agent.run("x").stop_reason is StopReason.MAX_TOKENS


def test_custom_stop_condition() -> None:
    model = FakeModel([FakeModel.tool_call("lookup", {"city": "x"}, text="step")], cycle=True)

    def stop(ctx: LoopContext) -> bool:
        return ctx.tool_calls >= 2

    agent = Agent(model=model, tools=[lookup], max_iterations=10, stop_when=stop)
    result = agent.run("go")
    assert result.stop_reason is StopReason.CUSTOM
    assert result.tool_calls == 2
    assert result.output == "step"  # salvaged from the last response


def test_structured_output_with_repair_call() -> None:
    class Verdict(BaseModel):
        approve: bool
        reason: str

    model = FakeModel(["I think we should approve it.", '{"approve": true, "reason": "healthy"}'])
    agent = Agent(model=model, output_schema=Verdict)
    result = agent.run("Should we approve?")
    assert result.structured == Verdict(approve=True, reason="healthy")
    assert model.calls == 2
    assert "Restate" in model.requests[1].messages[-1].text

    failing = FakeModel(["nope", "still nope"])
    with pytest.raises(StructuredOutputError):
        Agent(model=failing, output_schema=Verdict).run("q")


def test_errors_are_recorded_and_raised() -> None:
    sink = ListSink()
    agent = Agent(model=FakeModel([RuntimeError("model down")]))  # type: ignore[list-item]
    with pytest.raises(RuntimeError, match="model down"), start_run("t", sinks=[sink]) as run:
        agent.run("q")
    assert run.status is RunStatus.FAILED
    finished = sink.of_type(EventType.AGENT_LOOP_FINISHED)[0]
    assert finished.payload["stop_reason"] == "error"
    assert finished.payload["error"] == "RuntimeError: model down"


def test_agent_config_and_fingerprint_are_stable() -> None:
    a = Agent(model="fake:m", name="x", tools=[lookup], instructions="i")
    b = Agent(model="fake:m", name="x", tools=[lookup], instructions="i")
    assert a.fingerprint() == b.fingerprint()
    c = Agent(model="fake:m", name="x", tools=[lookup], instructions="different")
    assert a.fingerprint() != c.fingerprint()
    assert a.config()["tools"] == {"lookup": lookup.fingerprint()}
    with pytest.raises(ConfigurationError, match="unknown loop strategy"):
        Agent(model="fake:m", loop="nope")


async def test_async_run_and_state_round_trip() -> None:
    agent = Agent(model=FakeModel(["ok"]))
    result = await agent.arun("q", state={"customer": "acme"})
    assert result.state == {"customer": "acme"}
    assert result.output == "ok"
