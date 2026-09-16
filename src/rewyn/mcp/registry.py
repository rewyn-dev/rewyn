"""Registry of configured MCP servers and their clients."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from types import TracebackType
from typing import Any

from rewyn.core.types import ConfigurationError
from rewyn.mcp.client import MCPClient, MCPServerConfig
from rewyn.tools.tool import Tool


class MCPRegistry:
    def __init__(self, items: Iterable[MCPServerConfig | MCPClient] = ()) -> None:
        self._clients: dict[str, MCPClient] = {}
        for item in items:
            self.add(item)

    def add(self, item: MCPServerConfig | MCPClient, *, server: Any | None = None) -> MCPClient:
        client = item if isinstance(item, MCPClient) else MCPClient(item, server=server)
        if client.config.name in self._clients:
            raise ConfigurationError(f"MCP server {client.config.name!r} already registered")
        self._clients[client.config.name] = client
        return client

    def get(self, name: str) -> MCPClient:
        try:
            return self._clients[name]
        except KeyError:
            raise KeyError(f"unknown MCP server {name!r}") from None

    def names(self) -> list[str]:
        return list(self._clients)

    def __iter__(self) -> Iterator[MCPClient]:
        return iter(self._clients.values())

    def __len__(self) -> int:
        return len(self._clients)

    async def open_all(self) -> list[MCPClient]:
        for client in self._clients.values():
            await client.open()
        return list(self._clients.values())

    async def close_all(self) -> None:
        # Close in reverse order: transports use nested cancel scopes (LIFO).
        for client in reversed(list(self._clients.values())):
            await client.close()

    def tools(self) -> list[Tool]:
        tools: list[Tool] = []
        for client in self._clients.values():
            if client.opened:
                tools.extend(client._tools)
        return tools

    async def __aenter__(self) -> MCPRegistry:
        await self.open_all()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close_all()
