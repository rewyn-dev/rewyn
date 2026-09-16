"""Expose Rewyn tools as an MCP server."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any

from rewyn.core.types import MissingDependencyError
from rewyn.tools.tool import Tool, as_tool


def serve_tools(
    tools: Iterable[Tool | Callable[..., Any]],
    *,
    name: str = "rewyn",
    instructions: str | None = None,
    version: str = "1",
) -> Any:
    """Build an in-process ``MCPServer`` exposing ``tools``.

    Run it over stdio with :func:`run_stdio`, or connect in-process with
    ``rewyn.mcp.connect(server)``.
    """
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # pragma: no cover
        raise MissingDependencyError("mcp", "mcp") from exc
    server = MCPServer(name=name, instructions=instructions, version=version)
    for item in tools:
        tool = as_tool(item)
        server.add_tool(tool.fn, name=tool.name, description=tool.description or None)
    return server


def run_stdio(server: Any) -> None:
    """Serve ``server`` over stdio (blocking)."""
    asyncio.run(server.run_stdio_async())
