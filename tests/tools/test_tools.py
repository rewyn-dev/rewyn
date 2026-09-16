from __future__ import annotations

import asyncio

import pytest

from rewyn.core.event import EventType, ListSink
from rewyn.core.run import start_run
from rewyn.core.types import ConfigurationError
from rewyn.models.base import ToolCallPart
from rewyn.tools import (
    AllowList,
    CompositePolicy,
    MaxRiskLevel,
    PermissionDecision,
    RequirePermissions,
    RiskLevel,
    Tool,
    ToolArgumentError,
    ToolExecutionError,
    ToolExecutor,
    ToolRegistry,
    tool,
)


@tool
def add(a: int, b: int = 1) -> int:
    """Add two integers.

    Longer description that is not part of the summary.
    """
    return a + b


@tool(name="fetch", description="Fetch a record", risk_level="high", permissions=["db:read"])
async def fetch_record(record_id: str) -> dict[str, str]:
    await asyncio.sleep(0)
    return {"id": record_id}


def test_decorator_builds_metadata_and_schema() -> None:
    assert isinstance(add, Tool)
    assert add.name == "add"
    assert add.description == "Add two integers."
    assert add.parameters["properties"] == {
        "a": {"type": "integer"},
        "b": {"type": "integer", "default": 1},
    }
    assert add.parameters["required"] == ["a"]
    assert add.risk_level is RiskLevel.LOW
    assert add(2, 3) == 5  # direct call still works
    assert add.invoke(a=2) == 3
    assert add.fingerprint().startswith("sha256:")
    assert fetch_record.name == "fetch"
    assert fetch_record.is_async
    assert fetch_record.risk_level is RiskLevel.HIGH
    assert fetch_record.dependency.kind == "tool"


def test_argument_validation() -> None:
    with pytest.raises(ToolArgumentError, match="a: Field required"):
        add.invoke(b=2)
    with pytest.raises(ToolArgumentError, match="extra"):
        add.invoke(a=1, extra=2)
    assert add.invoke(a="3") == 4  # coerced by pydantic


def test_registry_rejects_duplicates_and_wraps_callables() -> None:
    def plain(x: int) -> int:
        return x

    registry = ToolRegistry([add, plain])
    assert registry.names() == ["add", "plain"]
    assert "plain" in registry
    assert registry["add"] is add
    with pytest.raises(ConfigurationError, match="already registered"):
        registry.register(add)
    registry.register(add, replace=True)
    with pytest.raises(KeyError):
        registry["missing"]
    assert [s.name for s in registry.specs()] == ["add", "plain"]


async def test_executor_emits_events_and_returns_results() -> None:
    sink = ListSink()
    executor = ToolExecutor(ToolRegistry([add, fetch_record]))
    async with start_run("t", sinks=[sink]) as run:
        results = await executor.execute_all(
            [
                ToolCallPart(id="c1", name="add", arguments={"a": 1, "b": 2}),
                ToolCallPart(id="c2", name="fetch", arguments={"record_id": "r"}),
                ToolCallPart(id="c3", name="nope", arguments={}),
                ToolCallPart(id="c4", name="add", arguments={"a": "x"}),
            ]
        )
    assert [r.tool_call_id for r in results] == ["c1", "c2", "c3", "c4"]
    assert results[0].content == 3
    assert results[1].content == {"id": "r"}
    assert results[2].is_error
    assert "unknown tool" in results[2].content
    assert results[3].is_error
    assert "invalid arguments" in results[3].content
    called = sink.of_type(EventType.TOOL_CALLED)
    returned = sink.of_type(EventType.TOOL_RETURNED)
    assert sorted(e.payload["name"] for e in called) == ["add", "add", "fetch", "nope"]
    by_id = {e.payload["tool_call_id"]: e.payload for e in called}
    assert by_id["c1"]["fingerprint"] == add.fingerprint()
    errors = {e.payload["tool_call_id"]: e.payload["is_error"] for e in returned}
    assert errors == {"c1": False, "c2": False, "c3": True, "c4": True}
    assert returned[0].payload["latency_ms"] >= 0
    assert run.manifest.usage.tool_calls == 4
    assert {d.name for d in run.manifest.dependencies} == {"add", "fetch"}
    tool_spans = [s for s in run.spans if s.kind.value == "tool"]
    assert len(tool_spans) == 4


async def test_executor_captures_exceptions_or_raises() -> None:
    @tool
    def boom() -> None:
        raise RuntimeError("kaboom")

    lenient = ToolExecutor(ToolRegistry([boom]))
    result = await lenient.execute(ToolCallPart(name="boom"))
    assert result.is_error
    assert result.content == "RuntimeError: kaboom"
    strict = ToolExecutor(ToolRegistry([boom]), raise_on_error=True)
    with pytest.raises(RuntimeError, match="kaboom"):
        await strict.execute(ToolCallPart(name="boom"))
    with pytest.raises(ToolExecutionError, match="unknown tool"):
        await strict.execute(ToolCallPart(name="missing"))


async def test_permission_policies_and_approval() -> None:
    sink = ListSink()
    registry = ToolRegistry([add, fetch_record])
    denied = ToolExecutor(registry, policy=AllowList(["add"]))
    async with start_run("t", sinks=[sink]):
        result = await denied.execute(
            ToolCallPart(id="c", name="fetch", arguments={"record_id": "1"})
        )
    assert result.is_error
    assert "not on the allow list" in result.content
    assert sink.of_type(EventType.TOOL_DENIED)[0].payload["policy"] == "allow_list"

    approvals: list[str] = []

    async def approver(t: Tool, args: dict[str, object], decision: PermissionDecision) -> bool:
        approvals.append(t.name)
        return t.name == "fetch"

    policy = CompositePolicy(
        [MaxRiskLevel(approve_above=RiskLevel.MEDIUM), RequirePermissions(["db:read"])]
    )
    executor = ToolExecutor(registry, policy=policy, approver=approver)
    ok = await executor.execute(ToolCallPart(name="fetch", arguments={"record_id": "9"}))
    assert not ok.is_error
    assert approvals == ["fetch"]
    no_approver = ToolExecutor(registry, policy=policy)
    refused = await no_approver.execute(ToolCallPart(name="fetch", arguments={"record_id": "9"}))
    assert refused.is_error
    assert "approval was not granted" in refused.content


def test_risk_levels_and_policies() -> None:
    assert RiskLevel.CRITICAL.rank > RiskLevel.HIGH.rank
    deny = MaxRiskLevel(RiskLevel.MEDIUM).check(fetch_record, {})
    assert not deny.allowed
    missing = RequirePermissions([]).check(fetch_record, {})
    assert "db:read" in missing.reason
    assert MaxRiskLevel().check(add, {}).allowed


async def test_tool_timeout() -> None:
    @tool(timeout=0.01)
    async def slow() -> None:
        await asyncio.sleep(1)

    result = await ToolExecutor(ToolRegistry([slow])).execute(ToolCallPart(name="slow"))
    assert result.is_error
    assert "TimeoutError" in result.content
