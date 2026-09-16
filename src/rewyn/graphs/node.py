"""Graph nodes: functions, agents, subgraphs and human approval gates."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from rewyn.core.schema import fingerprint
from rewyn.core.state import State
from rewyn.graphs.state import INPUT_KEY, OUTPUT_KEY, NodeContext

NodeKind = Literal["function", "agent", "subgraph"]
NodeFn = Callable[..., Any | Awaitable[Any]]


@dataclass(slots=True)
class Node:
    name: str
    target: Any
    kind: NodeKind = "function"
    retries: int = 0
    retry_delay: float = 0.0
    timeout: float | None = None
    approval: bool = False
    input_key: str = INPUT_KEY
    output_key: str | None = None
    version: str = "1"
    metadata: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        from rewyn.agents.agent import Agent
        from rewyn.graphs.graph import Graph

        if isinstance(self.target, Agent):
            inner: Any = self.target.fingerprint()
        elif isinstance(self.target, Graph):
            inner = self.target.fingerprint()
        else:
            inner = getattr(self.target, "__qualname__", repr(self.target))
        return fingerprint(
            {
                "name": self.name,
                "kind": self.kind,
                "target": inner,
                "retries": self.retries,
                "approval": self.approval,
                "version": self.version,
            }
        )

    async def invoke(self, ctx: NodeContext) -> Any:
        from rewyn.agents.agent import Agent
        from rewyn.graphs.graph import Graph

        state: State = ctx.state
        if isinstance(self.target, Agent):
            task = state.get(self.input_key)
            if task is None:
                task = state.get(OUTPUT_KEY, "")
            result = await self.target.arun(str(task), state=state)
            return {self.output_key or self.name: result.output, OUTPUT_KEY: result.output}
        if isinstance(self.target, Graph):
            graph_result = await self.target.arun(state)
            return dict(graph_result.state) | {self.output_key or self.name: graph_result.output}
        params = inspect.signature(self.target).parameters
        if params and next(iter(params.values())).annotation is NodeContext:
            value = self.target(ctx)
        elif "ctx" in params:
            value = self.target(ctx=ctx)
        else:
            value = self.target(state)
        if inspect.isawaitable(value):
            value = await value
        if self.output_key and not isinstance(value, dict):
            return {self.output_key: value, OUTPUT_KEY: value}
        return value


def make_node(name: str, target: Any, **options: Any) -> Node:
    from rewyn.agents.agent import Agent
    from rewyn.graphs.graph import Graph

    if isinstance(target, Agent):
        kind: NodeKind = "agent"
    elif isinstance(target, Graph):
        kind = "subgraph"
    elif callable(target):
        kind = "function"
    else:
        raise TypeError(f"node {name!r} target must be callable, an Agent or a Graph")
    return Node(name=name, target=target, kind=kind, **options)
