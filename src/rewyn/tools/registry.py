"""Tool registry: a named, ordered collection of tools."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any

from rewyn.core.types import ConfigurationError
from rewyn.models.base import ToolSpec
from rewyn.tools.tool import Tool, as_tool


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool | Callable[..., Any]] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for item in tools:
            self.register(item)

    def register(self, item: Tool | Callable[..., Any], *, replace: bool = False) -> Tool:
        tool = as_tool(item)
        if tool.name in self._tools and not replace:
            raise ConfigurationError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        return tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __getitem__(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"unknown tool {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    def merged(self, other: ToolRegistry) -> ToolRegistry:
        registry = ToolRegistry(self)
        for tool in other:
            registry.register(tool, replace=True)
        return registry
