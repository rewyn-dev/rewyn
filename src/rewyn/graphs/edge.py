"""Graph edges: sequential, conditional and routed."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from rewyn.core.state import State

END = "__end__"

Condition = Callable[[State], bool | Awaitable[bool]]
Router = Callable[[State], str | list[str] | Awaitable[str | list[str]]]


@dataclass(slots=True)
class Edge:
    source: str
    target: str
    condition: Condition | None = None
    label: str | None = None

    async def holds(self, state: State) -> bool:
        if self.condition is None:
            return True
        value = self.condition(state)
        if inspect.isawaitable(value):
            value = await value
        return bool(value)

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "conditional": self.condition is not None,
            "label": self.label or getattr(self.condition, "__name__", None),
        }


@dataclass(slots=True)
class Branch:
    """A router deciding the next node(s) from state."""

    source: str
    router: Router
    label: str | None = None

    async def route(self, state: State) -> list[str]:
        value = self.router(state)
        if inspect.isawaitable(value):
            value = await value
        return [value] if isinstance(value, str) else list(value)

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "router": self.label or getattr(self.router, "__name__", "router"),
        }
