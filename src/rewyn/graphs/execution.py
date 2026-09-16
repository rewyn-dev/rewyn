"""Graph execution engine: waves of parallel nodes with events and checkpoints."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any

from rewyn.core.event import EventType
from rewyn.core.run import Run, current_run, start_run
from rewyn.core.span import SpanKind
from rewyn.core.state import State
from rewyn.core.types import JSONObject, RewynError
from rewyn.graphs.edge import END
from rewyn.graphs.graph import Graph, GraphResult
from rewyn.graphs.node import Node
from rewyn.graphs.state import OUTPUT_KEY, PATH_KEY, NodeContext, apply_result
from rewyn.runtime.cancellation import check_cancelled
from rewyn.runtime.checkpoint import Checkpointer


class GraphExecutionError(RewynError):
    pass


class GraphRunner:
    def __init__(
        self,
        graph: Graph,
        *,
        checkpointer: Checkpointer | None = None,
        approval_handler: Any | None = None,
    ) -> None:
        self.graph = graph
        self.checkpointer = checkpointer
        self.approval_handler = approval_handler

    async def run(
        self, state_in: Mapping[str, Any] | State | None, *, resume_from: str | None
    ) -> GraphResult:
        active = current_run()
        if active is not None:
            return await self._execute(active, state_in, resume_from)
        async with start_run(self.graph.name) as run:
            result = await self._execute(run, state_in, resume_from)
            run.manifest.output = result.output
            return result

    async def _execute(
        self, run: Run, state_in: Mapping[str, Any] | State | None, resume_from: str | None
    ) -> GraphResult:
        graph = self.graph
        run.add_dependency(graph.dependency)
        state = state_in if isinstance(state_in, State) else State(state_in)
        frontier: list[str] = [graph.entry] if graph.entry else []
        step = 0
        path: list[str] = list(state.get(PATH_KEY, []))
        checkpoint_id: str | None = None

        if resume_from and self.checkpointer is not None:
            checkpoint = await self.checkpointer.restore(resume_from)
            state.restore(checkpoint.state)
            frontier = list(checkpoint.cursor.get("frontier", frontier))
            step = int(checkpoint.cursor.get("step", 0))
            path = list(checkpoint.cursor.get("path", path))

        with run.span(f"graph:{graph.name}", SpanKind.GRAPH, version=graph.version):
            run.emit(
                EventType.GRAPH_STARTED,
                {
                    "graph": graph.name,
                    "version": graph.version,
                    "fingerprint": graph.fingerprint(),
                    "entry": graph.entry,
                    "nodes": list(graph.nodes),
                    "resumed_from": resume_from,
                    "state_version": state.version,
                },
            )
            status = "completed"
            error: str | None = None
            try:
                while frontier:
                    check_cancelled()
                    if step >= graph.max_steps:
                        raise GraphExecutionError(
                            f"graph {graph.name!r} exceeded max_steps={graph.max_steps}"
                        )
                    step += 1
                    nodes = [graph.nodes[n] for n in frontier if n != END]
                    if not nodes:
                        break
                    results = await asyncio.gather(
                        *(self._run_node(run, node, state, step) for node in nodes)
                    )
                    path.extend(node.name for node in nodes)
                    state[PATH_KEY] = list(path)
                    frontier = await self._next_frontier(nodes, results, state)
                    if self.checkpointer is not None:
                        checkpoint = await self.checkpointer.save(
                            state=state,
                            cursor={"frontier": list(frontier), "step": step, "path": list(path)},
                            label=f"after step {step}",
                            owner=f"graph:{graph.name}",
                            run=run,
                        )
                        checkpoint_id = checkpoint.id
            except Exception as exc:
                status = "failed"
                error = f"{type(exc).__name__}: {exc}"
                run.emit(
                    EventType.GRAPH_FINISHED,
                    {
                        "graph": graph.name,
                        "status": status,
                        "steps": step,
                        "path": path,
                        "error": error,
                    },
                )
                raise
            run.emit(
                EventType.GRAPH_FINISHED,
                {
                    "graph": graph.name,
                    "status": status,
                    "steps": step,
                    "path": path,
                    "output": state.get(OUTPUT_KEY),
                    "state_version": state.version,
                },
            )
        return GraphResult(
            run_id=run.id,
            graph=graph.name,
            state=state.to_dict(),
            output=state.get(OUTPUT_KEY),
            path=path,
            steps=step,
            status=status,
            checkpoint_id=checkpoint_id,
            error=error,
        )

    async def _next_frontier(
        self, nodes: list[Node], results: list[dict[str, Any]], state: State
    ) -> list[str]:
        next_nodes: list[str] = []
        for node, _outcome in zip(nodes, results, strict=True):
            # A rejected approval gate still follows its edges; conditions on
            # ``<node>.approved`` let graphs route around it.
            branch = self.graph.branches.get(node.name)
            targets: list[str] = []
            if branch is not None:
                targets.extend(await branch.route(state))
            for edge in self.graph.edges:
                if edge.source == node.name and await edge.holds(state):
                    targets.append(edge.target)
            for target in targets:
                if target != END and target not in self.graph.nodes:
                    raise GraphExecutionError(f"router returned unknown node {target!r}")
                if target not in next_nodes:
                    next_nodes.append(target)
        return [n for n in next_nodes if n != END]

    async def _run_node(self, run: Run, node: Node, state: State, step: int) -> dict[str, Any]:
        ctx = NodeContext(run=run, state=state, node=node, step=step)
        with run.span(f"node:{node.name}", SpanKind.NODE, node_kind=node.kind):
            run.emit(
                EventType.GRAPH_NODE_STARTED,
                {
                    "graph": self.graph.name,
                    "node": node.name,
                    "kind": node.kind,
                    "step": step,
                    "fingerprint": node.fingerprint(),
                    "state_version": state.version,
                },
            )
            started = time.perf_counter()
            if node.approval:
                from rewyn.human.approval import approve

                decision = await approve(
                    f"node:{node.name}",
                    risk="high",
                    handler=self.approval_handler,
                    graph=self.graph.name,
                    step=step,
                )
                ctx.approved = decision.approved
                state[f"{node.name}.approved"] = decision.approved
                if not decision.approved:
                    run.emit(
                        EventType.GRAPH_NODE_FINISHED,
                        {
                            "graph": self.graph.name,
                            "node": node.name,
                            "step": step,
                            "skipped": True,
                            "reason": f"approval rejected: {decision.reason}",
                            "duration_ms": (time.perf_counter() - started) * 1000.0,
                        },
                    )
                    return {"skipped": True}
            attempt = 0
            while True:
                attempt += 1
                ctx.attempt = attempt
                try:
                    coro = node.invoke(ctx)
                    value = (
                        await asyncio.wait_for(coro, timeout=node.timeout)
                        if node.timeout
                        else await coro
                    )
                except Exception as exc:
                    failure: JSONObject = {
                        "graph": self.graph.name,
                        "node": node.name,
                        "step": step,
                        "attempt": attempt,
                        "error": f"{type(exc).__name__}: {exc}",
                        "will_retry": attempt <= node.retries,
                    }
                    run.emit(EventType.GRAPH_NODE_FAILED, failure)
                    if attempt > node.retries:
                        raise
                    if node.retry_delay:
                        await asyncio.sleep(node.retry_delay)
                    continue
                updates = apply_result(state, node.name, value)
                if updates:
                    run.emit(
                        EventType.STATE_UPDATED,
                        {
                            "graph": self.graph.name,
                            "node": node.name,
                            "keys": sorted(updates),
                            "version": state.version,
                            "fingerprint": state.snapshot().fingerprint,
                        },
                    )
                run.emit(
                    EventType.GRAPH_NODE_FINISHED,
                    {
                        "graph": self.graph.name,
                        "node": node.name,
                        "step": step,
                        "attempt": attempt,
                        "updates": sorted(updates),
                        "duration_ms": (time.perf_counter() - started) * 1000.0,
                    },
                )
                return {"updates": updates}
