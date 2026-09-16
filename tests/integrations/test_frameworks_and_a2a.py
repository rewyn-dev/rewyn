"""Framework interoperability and agent-to-agent calls (spec §3.2, §3.4, §51)."""

from __future__ import annotations

from typing import Any

import pytest

from rewyn import Agent, tool
from rewyn.core.event import EventType
from rewyn.core.run import start_run
from rewyn.integrations.a2a import (
    A2AClient,
    A2AError,
    A2AResponse,
    A2AServer,
    AgentCard,
    card_for,
    remote_agent_tool,
)
from rewyn.integrations.frameworks import (
    from_schema,
    instrument,
    recorded_run,
    to_anthropic_tools,
    to_callables,
    to_json_schema_tools,
    to_openai_tools,
)
from rewyn.testing import FakeModel


@tool
def search(query: str, limit: int = 5) -> list[str]:
    """Search the corpus for a query."""
    return [f"{query}-{i}" for i in range(limit)]


# Exporting tools ---------------------------------------------------------------
def test_tools_export_to_the_openai_shape():
    [exported] = to_openai_tools([search])
    assert exported["type"] == "function"
    assert exported["function"]["name"] == "search"
    assert exported["function"]["description"].startswith("Search the corpus")
    assert "query" in exported["function"]["parameters"]["properties"]


def test_tools_export_to_the_anthropic_shape():
    [exported] = to_anthropic_tools([search])
    assert exported["name"] == "search"
    assert "input_schema" in exported


def test_tools_export_to_a_plain_schema_shape():
    [exported] = to_json_schema_tools([search])
    assert set(exported) == {"name", "description", "parameters"}


def test_an_exported_callable_still_validates_its_arguments():
    """A foreign framework must not be able to bypass the tool's schema."""
    from rewyn.tools.tool import ToolArgumentError

    callables = to_callables([search])
    assert callables["search"](query="acme", limit=2) == ["acme-0", "acme-1"]
    with pytest.raises(ToolArgumentError):
        callables["search"](query=123, limit="many")


def test_a_bare_function_can_be_exported_too():
    def helper(x: str) -> str:
        """A plain function."""
        return x

    assert to_openai_tools([helper])[0]["function"]["name"] == "helper"


# Importing tools ---------------------------------------------------------------
def test_a_foreign_tool_definition_becomes_a_rewyn_tool():
    schema = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }
    imported = from_schema(
        "weather", "Look up the weather.", schema, lambda city: f"sunny in {city}"
    )

    assert imported.name == "weather"
    assert imported.parameters == schema
    assert imported.source == "external"
    assert imported.invoke(city="Sydney") == "sunny in Sydney"


def test_an_imported_tool_works_inside_an_agent():
    imported = from_schema(
        "weather",
        "Look up the weather.",
        {"type": "object", "properties": {"city": {"type": "string"}}},
        lambda city: f"sunny in {city}",
    )
    model = FakeModel([FakeModel.tool_call("weather", {"city": "Sydney"}), "It is sunny."])
    result = Agent(model, tools=[imported], name="weatherman").run("weather in Sydney?")
    assert result.output == "It is sunny."


# Recording foreign runs ---------------------------------------------------------
def test_instrumenting_a_sync_callable_records_a_run():
    def foreign_agent(question: str) -> str:
        return f"answered: {question}"

    wrapped = instrument(foreign_agent, framework="langgraph", version="3")
    with start_run("outer", record=False) as run:
        assert wrapped("what is EV share?") == "answered: what is EV share?"

    started = run.events_of(EventType.AGENT_LOOP_STARTED)[0]
    finished = run.events_of(EventType.AGENT_LOOP_FINISHED)[0]
    assert started.payload["framework"] == "langgraph"
    assert finished.payload["output"] == "answered: what is EV share?"
    assert finished.payload["stop_reason"] == "completed"
    assert finished.payload["duration_ms"] >= 0
    dependency = next(d for d in run.manifest.dependencies if d.kind == "agent")
    assert dependency.metadata["framework"] == "langgraph"


async def test_instrumenting_an_async_callable_records_a_run():
    async def foreign_agent(question: str) -> str:
        return "async answer"

    wrapped = instrument(foreign_agent, name="crew", framework="crewai")
    async with start_run("outer", record=False) as run:
        assert await wrapped("q") == "async answer"
    assert run.events_of(EventType.AGENT_LOOP_FINISHED)[0].payload["agent"] == "crew"


def test_a_foreign_failure_is_recorded_and_re_raised():
    def broken(_q: str) -> str:
        raise RuntimeError("framework exploded")

    wrapped = instrument(broken, framework="llamaindex")
    with start_run("outer", record=False) as run, pytest.raises(RuntimeError, match="exploded"):
        wrapped("q")
    finished = run.events_of(EventType.AGENT_LOOP_FINISHED)[0]
    assert finished.payload["stop_reason"] == "error"
    assert "framework exploded" in finished.payload["error"]


def test_instrumenting_preserves_the_callables_identity():
    def named(x: str) -> str:
        """Docstring survives."""
        return x

    wrapped = instrument(named)
    assert wrapped.__name__ == "named"
    assert wrapped.__doc__ == "Docstring survives."


def test_a_recorded_run_gives_foreign_code_somewhere_to_emit(rewyn_home):
    with recorded_run("my-langgraph-app", framework="langgraph") as run:
        Agent(FakeModel(["inner"]), name="inner").run("go")
    assert run.manifest.metadata["framework"] == "langgraph"
    assert run.events_of(EventType.MODEL_CALLED)


# A2A ----------------------------------------------------------------------------
def build_agent() -> Agent:
    return Agent(
        FakeModel(["the remote answer"], cycle=True),
        name="research-service",
        version="4",
        instructions="Research things for other services.",
        tools=[search],
    )


def test_an_agent_card_describes_what_it_offers():
    card = card_for(build_agent(), endpoint="https://research.internal/a2a")
    assert card.name == "research-service"
    assert card.version == "4"
    assert "search" in card.tools
    assert card.protocol_version == "1"
    assert card.fingerprint().startswith("sha256:")


def test_the_card_fingerprint_ignores_where_it_is_hosted():
    agent = build_agent()
    here = card_for(agent, endpoint="https://a.internal")
    there = card_for(agent, endpoint="https://b.internal")
    assert here.fingerprint() == there.fingerprint()


async def test_a_server_runs_the_agent_and_returns_its_answer():
    server = A2AServer(build_agent())
    body = await server.handle({"input": "what is EV share?", "run_id": "run_caller"})

    response = A2AResponse.model_validate(body)
    assert response.ok
    assert response.output == "the remote answer"
    assert response.agent == "research-service"
    assert response.run_id is not None


async def test_the_caller_run_id_is_carried_across_the_hop(rewyn_home):
    from rewyn.replay.recorder import RecordedRun
    from rewyn.runtime import default_recorder

    server = A2AServer(build_agent())
    body = await server.handle({"input": "q", "run_id": "run_caller"})
    default_recorder().flush()

    recorded = RecordedRun.load(body["run_id"])
    assert recorded.manifest.metadata["caller_run_id"] == "run_caller"
    assert "a2a" in recorded.manifest.tags


async def test_a_malformed_request_is_answered_not_raised():
    server = A2AServer(build_agent())
    body = await server.handle({"wrong": "shape"})
    assert body["error"].startswith("invalid request")


async def test_a_failing_remote_agent_returns_an_error_response():
    class Exploding:
        name = "boom"
        version = "1"
        instructions = ""
        skills = type("S", (), {"registry": []})()
        registry = type("R", (), {"names": lambda self: []})()

        async def arun(self, _input: Any) -> Any:
            raise RuntimeError("remote exploded")

    body = await A2AServer(Exploding()).handle({"input": "q"})
    assert body["stop_reason"] == "error"
    assert "remote exploded" in body["error"]


def test_the_server_publishes_its_card():
    described = A2AServer(build_agent(), endpoint="https://x/a2a").describe()
    assert described["name"] == "research-service"
    assert described["endpoint"] == "https://x/a2a"


class FakeTransport:
    """An httpx-shaped client that dispatches straight into an A2AServer."""

    def __init__(self, server: A2AServer) -> None:
        self.server = server
        self.headers_seen: list[dict[str, str]] = []

    async def post(self, url: str, *, json: dict, headers: dict) -> Any:
        self.headers_seen.append(headers)
        return _Response(200, await self.server.handle(json))

    async def get(self, url: str, *, headers: dict) -> Any:
        return _Response(200, self.server.describe())


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


async def test_a_client_calls_a_remote_agent_and_carries_the_run_id():
    transport = FakeTransport(A2AServer(build_agent()))
    client = A2AClient("https://research.internal/a2a", client=transport)

    async with start_run("caller", record=False) as run:
        response = await client.call("what is EV share?")

    assert response.ok
    assert response.output == "the remote answer"
    assert transport.headers_seen[0]["X-Rewyn-Run-Id"] == run.id


async def test_a_client_fetches_the_remote_card():
    client = A2AClient("https://x/a2a", client=FakeTransport(A2AServer(build_agent())))
    card = await client.describe()
    assert isinstance(card, AgentCard)
    assert card.name == "research-service"


async def test_a_transport_failure_becomes_an_error_response():
    class Broken:
        async def post(self, *_args: Any, **_kwargs: Any) -> Any:
            raise ConnectionError("no route to host")

    response = await A2AClient("https://x", client=Broken()).call("q")
    assert not response.ok
    assert "ConnectionError" in response.error


async def test_an_http_error_becomes_an_error_response():
    class Failing:
        async def post(self, *_args: Any, **_kwargs: Any) -> Any:
            return _Response(503, {})

    response = await A2AClient("https://x", client=Failing()).call("q")
    assert response.error == "HTTP 503"


async def test_a_remote_agent_is_callable_as_an_ordinary_tool():
    transport = FakeTransport(A2AServer(build_agent()))
    remote = remote_agent_tool(
        "https://research.internal/a2a",
        name="ask_research",
        description="Delegate research to the research service.",
        client=transport,
    )
    model = FakeModel([FakeModel.tool_call("ask_research", {"task": "EV share"}), "Done."])

    async with start_run("delegating", record=False) as run:
        result = await Agent(model, tools=[remote], name="local").arun("research EV share")

    assert result.output == "Done."
    returned = run.events_of(EventType.TOOL_RETURNED)[0]
    assert returned.payload["result"] == "the remote answer"
    assert returned.payload["name"] == "ask_research"


async def test_a_failing_remote_surfaces_through_the_tool():
    class Failing:
        async def post(self, *_args: Any, **_kwargs: Any) -> Any:
            return _Response(500, {})

    remote = remote_agent_tool("https://x", name="ask", description="d", client=Failing())
    with pytest.raises(A2AError, match="remote agent 'ask' failed"):
        await remote.ainvoke(task="anything")
