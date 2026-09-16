from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from rewyn import Agent, tool
from rewyn.core.event import EventType, ListSink
from rewyn.core.run import start_run
from rewyn.core.types import ConfigurationError
from rewyn.mcp import (
    MCPClient,
    MCPRegistry,
    MCPServerConfig,
    MCPToolError,
    connect,
    discover_configs,
    parse_mcp_config,
    serve_tools,
)
from rewyn.testing import FakeModel
from rewyn.tools import RiskLevel, ToolExecutor, ToolRegistry
from rewyn.tools.tool import ToolCall


def _demo_server() -> MCPServer:
    server = MCPServer(name="demo", version="2")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @server.tool()
    def fail(reason: str) -> str:
        """Always fails."""
        raise ValueError(reason)

    @server.tool()
    def profile(user: str) -> dict[str, Any]:
        """Return a structured profile."""
        return {"user": user, "tier": "gold"}

    return server


async def test_async_client_discovers_and_calls_tools() -> None:
    sink = ListSink()
    async with start_run("t", sinks=[sink]) as run, connect(_demo_server(), name="demo") as client:
        names = [t.name for t in client.tools()]
        assert names == ["add", "fail", "profile"]
        add_tool = client.tools()[0]
        assert add_tool.source == "mcp:demo"
        assert add_tool.parameters["properties"]["a"]["type"] == "integer"
        assert add_tool.risk_level is RiskLevel.MEDIUM
        assert await client.call_tool("add", {"a": 2, "b": 3}) == 5
        assert await client.call_tool("profile", {"user": "ann"}) == {"user": "ann", "tier": "gold"}
        with pytest.raises(MCPToolError, match="Error executing tool"):
            await client.call_tool("fail", {"reason": "boom"})
    types = [e.type for e in sink.events]
    assert EventType.MCP_CONNECTED in types
    assert EventType.MCP_TOOLS_DISCOVERED in types
    assert types[-2] is EventType.MCP_DISCONNECTED
    connected = sink.of_type(EventType.MCP_CONNECTED)[0]
    assert connected.payload["server"] == "demo"
    assert connected.payload["transport"] == "memory"
    assert connected.payload["server_info"]["name"] == "demo"
    called = sink.of_type(EventType.TOOL_CALLED)
    assert [e.payload["name"] for e in called] == ["add", "profile", "fail"]
    assert called[0].payload["source"] == "mcp:demo"
    returned = sink.of_type(EventType.TOOL_RETURNED)
    assert returned[0].payload["result"] == 5
    assert returned[2].payload["is_error"] is True
    assert {d.kind for d in run.manifest.dependencies} == {"mcp_server", "tool"}


def test_sync_facade_opens_on_demand() -> None:
    client = connect(_demo_server(), name="demo", tool_prefix="demo_")
    try:
        tools = client.tools()  # opens via background loop
        assert client.opened
        assert [t.name for t in tools] == ["demo_add", "demo_fail", "demo_profile"]
        assert tools[0].invoke(a=1, b=1) == 2
        assert client.call_tool_sync("add", {"a": 5, "b": 5}) == 10
    finally:
        client.close_sync()
    assert not client.opened


async def test_mcp_tools_run_through_agent() -> None:
    model = FakeModel([FakeModel.tool_call("add", {"a": 40, "b": 2}), "The answer is 42."])
    sink = ListSink()
    async with Agent(model=model, mcp=[connect(_demo_server(), name="demo")]) as agent:
        async with start_run("t", sinks=[sink]):
            result = await agent.arun("add 40 and 2")
        assert result.output == "The answer is 42."
        assert result.messages[2].tool_results[0].content == 42
        assert "add" in agent.registry
        assert agent.mcp_clients[0].opened
    assert not agent.mcp_clients[0].opened
    assert sink.of_type(EventType.TOOL_CALLED)[0].payload["source"] == "mcp:demo"


async def test_serve_tools_round_trip() -> None:
    @tool(risk_level="high")
    def shout(text: str) -> str:
        """Upper-case text."""
        return text.upper()

    server = serve_tools([shout], name="rewyn-demo")
    async with connect(server) as client:
        assert client.config.name == "rewyn-demo"
        [remote] = client.tools()
        assert remote.name == "shout"
        assert remote.description == "Upper-case text."
        executor = ToolExecutor(ToolRegistry([remote]))
        result = await executor.execute(ToolCall(name="shout", arguments={"text": "hi"}))
        assert result.content == "HI"


def test_config_validation_and_connect_variants() -> None:
    with pytest.raises(ConfigurationError, match="in-process"):
        MCPClient(MCPServerConfig(name="x", transport="memory"))
    with pytest.raises(ConfigurationError, match="command"):
        MCPClient(MCPServerConfig(name="x", transport="stdio"))
    with pytest.raises(ConfigurationError, match="url"):
        MCPClient(MCPServerConfig(name="x", transport="http"))
    with pytest.raises(ConfigurationError, match="needs"):
        connect()
    stdio = connect("npx -y some-server --flag", name="s")
    assert stdio.config.transport == "stdio"
    assert stdio.config.command == "npx"
    assert stdio.config.args == ["-y", "some-server", "--flag"]
    http = connect("https://example.com/mcp")
    assert http.config.transport == "http"
    assert http.config.name == "https://example.com/mcp"
    assert stdio.config.fingerprint() != http.config.fingerprint()
    assert stdio.config.dependency.kind == "mcp_server"


def test_parse_and_discover_configs(tmp_path: Path) -> None:
    claude_style = {
        "mcpServers": {
            "github": {"command": "npx", "args": ["-y", "gh"], "env": {"TOKEN": "x"}},
            "crm": {"url": "http://localhost:9000/mcp"},
            "broken": {"nothing": True},
        }
    }
    configs = parse_mcp_config(claude_style)
    assert [c.name for c in configs] == ["github", "crm"]
    assert configs[0].transport == "stdio"
    assert configs[0].env == {"TOKEN": "x"}
    assert configs[1].transport == "http"
    (tmp_path / "a.json").write_text(json.dumps(claude_style))
    (tmp_path / "b.json").write_text(
        json.dumps({"servers": [{"name": "github", "transport": "http", "url": "http://x"}]})
    )
    (tmp_path / "bad.json").write_text("not json")
    found = discover_configs([tmp_path / "a.json", tmp_path / "b.json", tmp_path / "bad.json"])
    assert [c.name for c in found] == ["github", "crm"]
    assert found[0].transport == "stdio"  # first definition wins


async def test_registry_opens_and_closes_all() -> None:
    registry = MCPRegistry([connect(_demo_server(), name="one")])
    registry.add(MCPServerConfig(name="two", transport="memory"), server=_demo_server())
    with pytest.raises(ConfigurationError, match="already registered"):
        registry.add(MCPServerConfig(name="one", transport="memory"), server=_demo_server())
    assert registry.names() == ["one", "two"]
    assert registry.tools() == []
    async with registry:
        assert len(registry.tools()) == 6
        assert registry.get("two").opened
    assert not registry.get("one").opened
    with pytest.raises(KeyError):
        registry.get("three")
