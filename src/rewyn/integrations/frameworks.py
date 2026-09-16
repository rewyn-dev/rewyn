"""Using Rewyn with other agent frameworks (spec §3.2, §51).

Framework independence cuts two ways. Rewyn does not depend on LangGraph,
LlamaIndex, CrewAI or anyone else, and none of this module imports them. But
"we don't depend on them" is not the same as "you can use us together", and
the second is the one that matters to someone who already has an agent.

Three things are needed, and none of them require the other framework to be
installed:

**Give them your tools.** :func:`to_openai_tools` and friends emit the plain
dictionaries every framework accepts. A Rewyn `Tool` keeps its schema,
description and validation wherever it is called from.

**Record their runs.** :func:`instrument` wraps any callable so its
execution becomes a Rewyn run with real events, cost and a replayable
log. The foreign agent does not know it is being recorded.

**Take their tools.** :func:`from_schema` turns a foreign tool definition
into a Rewyn `Tool`, so an existing toolset works inside a Rewyn agent.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Sequence
from typing import Any

from rewyn.core.event import EventType
from rewyn.core.run import Run, aensure_run, start_run
from rewyn.core.schema import to_jsonable
from rewyn.core.span import SpanKind
from rewyn.core.types import JSONObject
from rewyn.tools.tool import Tool, as_tool


# Exporting tools ---------------------------------------------------------------
def to_openai_tools(tools: Sequence[Tool | Callable[..., Any]]) -> list[JSONObject]:
    """OpenAI / OpenAI Agents SDK function-tool format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in (as_tool(x) for x in tools)
    ]


def to_anthropic_tools(tools: Sequence[Tool | Callable[..., Any]]) -> list[JSONObject]:
    """Anthropic tool format."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in (as_tool(x) for x in tools)
    ]


def to_json_schema_tools(tools: Sequence[Tool | Callable[..., Any]]) -> list[JSONObject]:
    """Plain name/description/schema, which LangChain, LlamaIndex, CrewAI and
    PydanticAI all accept in one shape or another."""
    return [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in (as_tool(x) for x in tools)
    ]


def to_callables(tools: Sequence[Tool | Callable[..., Any]]) -> dict[str, Callable[..., Any]]:
    """Name to validating callable, for frameworks that want to call directly.

    The callable still validates arguments against the schema, so a foreign
    framework cannot slip past the checks a Rewyn tool carries.
    """
    resolved = [as_tool(x) for x in tools]
    return {t.name: _validating(t) for t in resolved}


def _validating(tool: Tool) -> Callable[..., Any]:
    def call(**arguments: Any) -> Any:
        return tool.invoke(**arguments)

    call.__name__ = tool.name
    call.__doc__ = tool.description
    return call


# Importing tools ---------------------------------------------------------------
def from_schema(
    name: str,
    description: str,
    parameters: JSONObject,
    fn: Callable[..., Any],
    **options: Any,
) -> Tool:
    """Wrap a foreign tool definition as a Rewyn :class:`Tool`.

    Use this when the schema is authoritative and cannot be derived from the
    Python signature, which is the usual case for a tool that came from
    somewhere else.
    """
    from rewyn.tools.tool import make_tool

    built = make_tool(fn, name=name, description=description, parameters=parameters, **options)
    built.source = "external"
    return built


# Recording foreign runs ---------------------------------------------------------
def instrument(
    fn: Callable[..., Any],
    *,
    name: str | None = None,
    framework: str = "external",
    version: str = "1",
) -> Callable[..., Any]:
    """Wrap a callable so each invocation becomes a recorded Rewyn run.

    The wrapped thing can be a LangGraph graph's ``invoke``, a CrewAI crew's
    ``kickoff``, or any function at all. What Rewyn records is the input,
    the output, the duration and the failure; it cannot see inside, so it
    does not pretend to. Model and tool calls made through Rewyn primitives
    inside the callable are recorded in full as usual.
    """
    label: str = name or str(getattr(fn, "__name__", framework))

    async def _record(run: Run, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        run.add_dependency(_dependency(framework, label, version))
        with run.span(f"{framework}:{label}", SpanKind.CUSTOM, framework=framework):
            started = time.perf_counter()
            run.emit(
                EventType.AGENT_LOOP_STARTED,
                {
                    "agent": label,
                    "framework": framework,
                    "version": version,
                    "input": to_jsonable({"args": args, "kwargs": kwargs}),
                },
            )
            try:
                outcome = fn(*args, **kwargs)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
            except Exception as exc:
                run.emit(
                    EventType.AGENT_LOOP_FINISHED,
                    {
                        "agent": label,
                        "framework": framework,
                        "stop_reason": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "duration_ms": (time.perf_counter() - started) * 1000.0,
                    },
                )
                raise
            run.emit(
                EventType.AGENT_LOOP_FINISHED,
                {
                    "agent": label,
                    "framework": framework,
                    "stop_reason": "completed",
                    "output": to_jsonable(outcome),
                    "duration_ms": (time.perf_counter() - started) * 1000.0,
                },
            )
            return outcome

    if inspect.iscoroutinefunction(fn):

        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            async with aensure_run(label) as run:
                return await _record(run, args, kwargs)

        return _named(awrapper, fn)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        from rewyn.core.sync import run_sync

        async def inner() -> Any:
            async with aensure_run(label) as run:
                return await _record(run, args, kwargs)

        return run_sync(inner())

    return _named(wrapper, fn)


def _named(wrapper: Callable[..., Any], original: Callable[..., Any]) -> Callable[..., Any]:
    wrapper.__name__ = getattr(original, "__name__", "instrumented")
    wrapper.__doc__ = original.__doc__
    return wrapper


def _dependency(framework: str, name: str, version: str) -> Any:
    from rewyn.core.run import DependencyRef

    return DependencyRef(
        kind="agent", name=name, version=version, metadata={"framework": framework}
    )


def recorded_run(name: str, *, framework: str = "external", **metadata: Any) -> Run:
    """Start a run for work Rewyn does not otherwise see.

    ``with recorded_run("my-langgraph-app") as run:`` gives foreign code a
    run to emit into, so anything it does through Rewyn primitives lands in
    one trace.
    """
    return start_run(name, metadata={"framework": framework, **metadata})
