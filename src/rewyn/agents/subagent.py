"""Subagents (spec §18): agents delegating to agents inside one run tree.

Each subagent invocation gets its own span, ``SUBAGENT_STARTED`` /
``SUBAGENT_FINISHED`` events, and a fresh transcript. The parent run keeps
the complete tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rewyn.core.event import EventType
from rewyn.core.run import aensure_run
from rewyn.core.span import SpanKind
from rewyn.core.state import State
from rewyn.tools.tool import Tool, make_tool

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.agents.agent import Agent, RunResult


async def run_subagent(
    agent: Agent, task: str, *, parent: str | None = None, state: State | None = None
) -> RunResult:
    async with aensure_run(agent.name) as run:
        with run.span(f"subagent:{agent.name}", SpanKind.SUBAGENT, parent=parent):
            run.emit(
                EventType.SUBAGENT_STARTED,
                {
                    "subagent": agent.name,
                    "parent": parent,
                    "version": agent.version,
                    "fingerprint": agent.fingerprint(),
                    "task": task,
                },
            )
            try:
                result = await agent.arun(task, state=state)
            except Exception as exc:
                run.emit(
                    EventType.SUBAGENT_FINISHED,
                    {
                        "subagent": agent.name,
                        "parent": parent,
                        "stop_reason": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                raise
            run.emit(
                EventType.SUBAGENT_FINISHED,
                {
                    "subagent": agent.name,
                    "parent": parent,
                    "stop_reason": result.stop_reason.value,
                    "iterations": result.iterations,
                    "tool_calls": result.tool_calls,
                    "usage": result.usage.model_dump(),
                    "cost": result.cost,
                    "output": result.output,
                },
            )
            return result


def subagent_tool(
    agent: Agent,
    *,
    parent: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> Tool:
    """Expose ``agent`` as a ``delegate_to_<name>(task)`` tool."""
    tool_name = name or f"delegate_to_{_slug(agent.name)}"
    doc = description or (
        f"Delegate a self-contained task to the {agent.name!r} agent and return its answer."
    )

    async def delegate(task: str) -> Any:
        result = await run_subagent(agent, task, parent=parent)
        return result.structured if result.structured is not None else result.output

    return make_tool(delegate, name=tool_name, description=doc, tags=["subagent"])


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower() or "agent"
