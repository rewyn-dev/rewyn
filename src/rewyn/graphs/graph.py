"""Graph engineering (spec §16).

::

    graph = Graph("research")
    graph.add_node("research", researcher)
    graph.add_node("analyze", analyst)
    graph.add_node("write", writer)
    graph.connect("research", "analyze")
    graph.connect("analyze", "write")
    result = graph.run({"input": "EV market"})

Supports sequential nodes, conditional edges, routers (branching), loops,
parallel fan-out/fan-in, retries, timeouts, checkpoints, human approval
gates and subgraphs. Execution emits ``GRAPH_*`` and ``STATE_UPDATED`` events.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.run import DependencyRef
from rewyn.core.schema import fingerprint
from rewyn.core.state import State
from rewyn.core.sync import run_sync
from rewyn.core.types import ConfigurationError, JSONObject
from rewyn.graphs.edge import END, Branch, Condition, Edge, Router
from rewyn.graphs.node import Node, make_node
from rewyn.graphs.state import INPUT_KEY, OUTPUT_KEY
from rewyn.runtime.checkpoint import Checkpointer


class GraphResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    graph: str
    state: JSONObject
    output: Any = None
    path: list[str] = Field(default_factory=list)
    steps: int = 0
    status: str = "completed"
    checkpoint_id: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "completed"


class Graph:
    def __init__(self, name: str = "graph", *, version: str = "1", max_steps: int = 100) -> None:
        self.name = name
        self.version = version
        self.max_steps = max_steps
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.branches: dict[str, Branch] = {}
        self.entry: str | None = None

    # Building --------------------------------------------------------------------
    def add_node(self, name: str, target: Any, **options: Any) -> Node:
        if name in self.nodes or name == END:
            raise ConfigurationError(f"node {name!r} already exists or is reserved")
        node = make_node(name, target, **options)
        self.nodes[name] = node
        if self.entry is None:
            self.entry = name
        return node

    def add_subgraph(self, name: str, graph: Graph, **options: Any) -> Node:
        return self.add_node(name, graph, **options)

    def connect(
        self, source: str, target: str, *, when: Condition | None = None, label: str | None = None
    ) -> Edge:
        self._check(source)
        if target != END:
            self._check(target)
        edge = Edge(source=source, target=target, condition=when, label=label)
        self.edges.append(edge)
        return edge

    def branch(self, source: str, router: Router, *, label: str | None = None) -> Branch:
        self._check(source)
        self.branches[source] = Branch(source=source, router=router, label=label)
        return self.branches[source]

    def set_entry(self, name: str) -> Graph:
        self._check(name)
        self.entry = name
        return self

    def _check(self, name: str) -> None:
        if name not in self.nodes:
            raise ConfigurationError(f"unknown node {name!r}")

    # Identity ---------------------------------------------------------------------
    def config(self) -> JSONObject:
        return {
            "name": self.name,
            "version": self.version,
            "entry": self.entry,
            "nodes": {n: node.fingerprint() for n, node in self.nodes.items()},
            "edges": [e.describe() for e in self.edges],
            "branches": [b.describe() for b in self.branches.values()],
            "max_steps": self.max_steps,
        }

    def fingerprint(self) -> str:
        return fingerprint(self.config())

    @property
    def dependency(self) -> DependencyRef:
        return DependencyRef(
            kind="graph", name=self.name, version=self.version, fingerprint=self.fingerprint()
        )

    # Execution --------------------------------------------------------------------
    async def arun(
        self,
        state: Mapping[str, Any] | State | str | None = None,
        *,
        checkpointer: Checkpointer | None = None,
        resume_from: str | None = None,
        approval_handler: Any | None = None,
    ) -> GraphResult:
        from rewyn.graphs.execution import GraphRunner

        if isinstance(state, str):
            state = {INPUT_KEY: state}
        runner = GraphRunner(self, checkpointer=checkpointer, approval_handler=approval_handler)
        return await runner.run(state, resume_from=resume_from)

    def run(
        self, state: Mapping[str, Any] | State | str | None = None, **kwargs: Any
    ) -> GraphResult:
        return run_sync(self.arun(state, **kwargs))

    async def astream(
        self,
        state: Mapping[str, Any] | State | str | None = None,
        *,
        tags: Sequence[str] = (),
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Yield graph progress live; the final item is the :class:`GraphResult`.

        Node starts and finishes, model and tool events from agent nodes,
        token deltas and tool progress all arrive through one iterator, which
        is the same stream ``Agent.astream`` uses (spec §41).
        """
        from rewyn.core.event import EventType
        from rewyn.core.run import RunStatus, current_run, start_run
        from rewyn.runtime.streaming import EventStream

        active = current_run()
        run = active if active is not None else start_run(self.name, tags=tags, input=state)
        try:
            with EventStream(run, stop_on={EventType.GRAPH_FINISHED}) as stream:
                if active is None:
                    run.start(input=state)
                task = asyncio.ensure_future(self.arun(state, **kwargs))
                async for item in stream:
                    yield item
                result = await task
            yield result
        except BaseException as exc:
            if active is None:
                run.finish(status=RunStatus.FAILED, error=exc)
            raise
        else:
            if active is None:
                run.manifest.output = result.output
                run.finish()

    def output_of(self, result: GraphResult) -> Any:
        return result.state.get(OUTPUT_KEY)
