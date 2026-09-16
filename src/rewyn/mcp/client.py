"""MCP client (spec §12).

::

    server = rewyn.mcp.connect("npx -y @modelcontextprotocol/server-github", name="github")
    tools = server.tools()          # Rewyn Tool objects, usable by any Agent

    async with rewyn.mcp.connect(url="http://localhost:8000/mcp", name="crm") as crm:
        result = await crm.call_tool("lookup", {"id": "42"})

Connections, discovery and tool calls become Rewyn events; MCP tools
execute through the normal tool executor so they appear in the execution
graph with permissions, timing and results.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import shlex
import time
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import EventType
from rewyn.core.run import DependencyRef, aensure_run
from rewyn.core.schema import fingerprint
from rewyn.core.span import SpanKind
from rewyn.core.sync import BackgroundLoop
from rewyn.core.types import ConfigurationError, JSONObject, MissingDependencyError
from rewyn.mcp.adapter import MCPToolError, mcp_tool_to_rewyn, result_to_value
from rewyn.tools.permissions import RiskLevel
from rewyn.tools.tool import Tool

Transport = Literal["stdio", "http", "memory"]


class MCPServerConfig(BaseModel):
    """Versionable description of how to reach an MCP server."""

    model_config = ConfigDict(extra="forbid")

    name: str
    transport: Transport = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    version: str = "1"
    tool_prefix: str | None = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    metadata: JSONObject = Field(default_factory=dict)

    def fingerprint(self) -> str:
        return fingerprint(self.model_dump(mode="json", exclude={"env", "metadata"}))

    @property
    def dependency(self) -> DependencyRef:
        return DependencyRef(
            kind="mcp_server",
            name=self.name,
            version=self.version,
            fingerprint=self.fingerprint(),
            metadata={"transport": self.transport},
        )


class MCPClient:
    def __init__(self, config: MCPServerConfig, *, server: Any | None = None) -> None:
        if config.transport == "memory" and server is None:
            raise ConfigurationError("memory transport requires an in-process MCP server")
        if config.transport == "stdio" and not config.command:
            raise ConfigurationError("stdio transport requires a command")
        if config.transport == "http" and not config.url:
            raise ConfigurationError("http transport requires a url")
        self.config = config
        self._server = server
        self._client: Any | None = None
        self._tools: list[Tool] = []
        self._bg: BackgroundLoop | None = None
        self._stop: asyncio.Event | None = None
        self._serving: concurrent.futures.Future[None] | None = None
        self.server_info: JSONObject = {}
        self.opened = False

    # Connection ----------------------------------------------------------------
    def _build_client(self) -> Any:
        try:
            from mcp.client.client import Client
        except ImportError as exc:  # pragma: no cover
            raise MissingDependencyError("mcp", "mcp") from exc
        if self.config.transport == "memory":
            assert self._server is not None
            return Client(self._server)
        if self.config.transport == "stdio":
            from mcp.client.stdio import StdioServerParameters

            params = StdioServerParameters(
                command=self.config.command or "",
                args=list(self.config.args),
                env=dict(self.config.env) or None,
                cwd=self.config.cwd,
            )
            return Client(params)
        return Client(self.config.url or "")

    async def open(self) -> MCPClient:
        if self.opened:
            return self
        async with aensure_run("mcp") as run:
            with run.span(f"mcp:connect:{self.config.name}", SpanKind.MCP):
                started = time.perf_counter()
                self._client = self._build_client()
                await self._client.__aenter__()
                info = getattr(self._client, "server_info", None)
                self.server_info = (
                    {"name": getattr(info, "name", None), "version": getattr(info, "version", None)}
                    if info is not None
                    else {}
                )
                self.opened = True
                run.add_dependency(self.config.dependency)
                run.emit(
                    EventType.MCP_CONNECTED,
                    {
                        "server": self.config.name,
                        "transport": self.config.transport,
                        "server_info": self.server_info,
                        "fingerprint": self.config.fingerprint(),
                        "latency_ms": (time.perf_counter() - started) * 1000.0,
                    },
                )
                await self.discover_tools()
        return self

    async def close(self) -> None:
        if not self.opened or self._client is None:
            return
        async with aensure_run("mcp") as run:
            try:
                await self._client.__aexit__(None, None, None)
            finally:
                self.opened = False
                self._client = None
                run.emit(EventType.MCP_DISCONNECTED, {"server": self.config.name})

    async def __aenter__(self) -> MCPClient:
        return await self.open()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # Sync facade ---------------------------------------------------------------
    async def _serve(self, ready: concurrent.futures.Future[bool]) -> None:
        """Own the connection for its whole lifetime on one task (anyio cancel scopes)."""
        self._stop = asyncio.Event()
        try:
            await self.open()
        except BaseException as exc:
            ready.set_exception(exc)
            return
        ready.set_result(True)
        try:
            await self._stop.wait()
        finally:
            await self.close()

    def open_sync(self) -> MCPClient:
        if self.opened:
            return self
        self._bg = BackgroundLoop(name=f"rewyn-mcp-{self.config.name}")
        ready: concurrent.futures.Future[bool] = concurrent.futures.Future()
        self._serving = self._bg.submit(self._serve(ready))
        ready.result(timeout=60)
        return self

    def close_sync(self) -> None:
        if self._bg is None:
            return
        if self._stop is not None:
            self._bg.loop.call_soon_threadsafe(self._stop.set)
        if self._serving is not None:
            self._serving.result(timeout=30)
        self._bg.stop()
        self._bg = None
        self._stop = None
        self._serving = None

    def __enter__(self) -> MCPClient:
        return self.open_sync()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close_sync()

    async def _on_loop(self, coro: Any) -> Any:
        """Run ``coro`` on the connection's loop (background loop for sync clients)."""
        if self._bg is not None:
            return await self._bg.call(coro)
        return await coro

    def _require_client(self) -> Any:
        if not self.opened or self._client is None:
            raise ConfigurationError(
                f"MCP server {self.config.name!r} is not connected; use 'async with' or open()"
            )
        return self._client

    # Discovery -----------------------------------------------------------------
    async def discover_tools(self) -> list[Tool]:
        client = self._require_client()

        async def _list() -> Any:
            return await client.list_tools()

        result = await self._on_loop(_list())
        self._tools = [
            mcp_tool_to_rewyn(
                self, t, prefix=self.config.tool_prefix, risk_level=self.config.risk_level
            )
            for t in getattr(result, "tools", None) or []
        ]
        async with aensure_run("mcp") as run:
            run.emit(
                EventType.MCP_TOOLS_DISCOVERED,
                {
                    "server": self.config.name,
                    "count": len(self._tools),
                    "tools": [
                        {"name": t.name, "fingerprint": t.fingerprint(), "risk_level": t.risk_level}
                        for t in self._tools
                    ],
                },
            )
        return list(self._tools)

    def tools(self) -> list[Tool]:
        """Discovered tools. Opens the connection synchronously if needed."""
        if not self.opened:
            self.open_sync()
        return list(self._tools)

    async def list_resources(self) -> list[JSONObject]:
        client = self._require_client()
        result = await self._on_loop(client.list_resources())
        return [
            r.model_dump(mode="json", exclude_none=True) for r in getattr(result, "resources", [])
        ]

    async def read_resource(self, uri: str) -> list[JSONObject]:
        client = self._require_client()
        result = await self._on_loop(client.read_resource(uri))
        return [
            c.model_dump(mode="json", exclude_none=True) for c in getattr(result, "contents", [])
        ]

    async def list_prompts(self) -> list[JSONObject]:
        client = self._require_client()
        result = await self._on_loop(client.list_prompts())
        return [
            p.model_dump(mode="json", exclude_none=True) for p in getattr(result, "prompts", [])
        ]

    async def get_prompt(self, name: str, arguments: Mapping[str, str] | None = None) -> JSONObject:
        client = self._require_client()
        result = await self._on_loop(client.get_prompt(name, dict(arguments or {})))
        return dict(result.model_dump(mode="json", exclude_none=True))

    # Tool calls ----------------------------------------------------------------
    async def call_tool_raw(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        """Call a remote tool without emitting events (the executor emits them)."""
        client = self._require_client()
        result = await self._on_loop(client.call_tool(name, dict(arguments or {})))
        if getattr(result, "is_error", False):
            raise MCPToolError(str(result_to_value(result)))
        return result_to_value(result)

    async def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        """Call a remote tool directly, recording ``TOOL_CALLED``/``TOOL_RETURNED``."""
        from rewyn.tools.execution import ToolExecutor
        from rewyn.tools.registry import ToolRegistry
        from rewyn.tools.tool import ToolCall

        local = f"{self.config.tool_prefix or ''}{name}"
        executor = ToolExecutor(ToolRegistry(self._tools), raise_on_error=True)
        result = await executor.execute(ToolCall(name=local, arguments=dict(arguments or {})))
        return result.content

    def call_tool_sync(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        from rewyn.core.sync import run_sync

        if not self.opened:
            self.open_sync()
        return run_sync(self.call_tool(name, arguments))


def connect(
    target: str | MCPServerConfig | Any | None = None,
    *,
    name: str | None = None,
    url: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    version: str = "1",
    tool_prefix: str | None = None,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
) -> MCPClient:
    """Create an :class:`MCPClient` from a config, URL, command line or in-process server.

    The returned client is not yet connected: use ``async with``, ``with``,
    ``await client.open()`` or call ``client.tools()`` (which connects).
    """
    if isinstance(target, MCPServerConfig):
        return MCPClient(target)
    server: Any | None = None
    if isinstance(target, str):
        if target.startswith(("http://", "https://")):
            url = target
        else:
            parts = shlex.split(target)
            command, args = parts[0], parts[1:] + (args or [])
    elif target is not None:
        server = target
    if server is not None:
        config = MCPServerConfig(
            name=name or getattr(server, "name", None) or "memory",
            transport="memory",
            version=version,
            tool_prefix=tool_prefix,
            risk_level=risk_level,
        )
        return MCPClient(config, server=server)
    if url:
        config = MCPServerConfig(
            name=name or url,
            transport="http",
            url=url,
            version=version,
            tool_prefix=tool_prefix,
            risk_level=risk_level,
        )
        return MCPClient(config)
    if command:
        config = MCPServerConfig(
            name=name or command,
            transport="stdio",
            command=command,
            args=list(args or []),
            env=dict(env or {}),
            cwd=cwd,
            version=version,
            tool_prefix=tool_prefix,
            risk_level=risk_level,
        )
        return MCPClient(config)
    raise ConfigurationError("connect() needs a config, url, command or in-process server")
