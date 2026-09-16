"""Tool execution with events, permissions and error capture.

Every call emits ``TOOL_CALLED`` and then ``TOOL_RETURNED`` (or
``TOOL_DENIED``). Failures become error results returned to the model by
default so the agent can recover; ``raise_on_error=True`` propagates them.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from rewyn.core.event import EventType
from rewyn.core.run import Run, aensure_run
from rewyn.core.schema import to_jsonable
from rewyn.core.span import SpanKind
from rewyn.core.types import JSONObject
from rewyn.tools.permissions import AllowAll, PermissionDecision, PermissionPolicy
from rewyn.tools.registry import ToolRegistry
from rewyn.tools.tool import Tool, ToolArgumentError, ToolCall, ToolResult

Approver = Callable[[Tool, JSONObject, PermissionDecision], Awaitable[bool]]


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        policy: PermissionPolicy | None = None,
        raise_on_error: bool = False,
        max_concurrency: int = 8,
        approver: Approver | None = None,
    ) -> None:
        self.registry = registry
        self.policy: PermissionPolicy = policy or AllowAll()
        self.raise_on_error = raise_on_error
        self.max_concurrency = max_concurrency
        self.approver = approver

    async def execute_all(self, calls: Sequence[ToolCall]) -> list[ToolResult]:
        """Execute calls concurrently (bounded), preserving order."""
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _one(call: ToolCall) -> ToolResult:
            async with semaphore:
                return await self.execute(call)

        return list(await asyncio.gather(*(_one(c) for c in calls)))

    async def execute(self, call: ToolCall) -> ToolResult:
        async with aensure_run("tool") as run:
            tool = self.registry.get(call.name)
            with run.span(f"tool:{call.name}", SpanKind.TOOL, tool_call_id=call.id):
                run.emit(
                    EventType.TOOL_CALLED,
                    {
                        "tool_call_id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "version": tool.version if tool else None,
                        "fingerprint": tool.fingerprint() if tool else None,
                        "risk_level": tool.risk_level.value if tool else None,
                        "source": tool.source if tool else None,
                    },
                )
                started = time.perf_counter()
                if tool is None:
                    return self._error(run, call, started, f"unknown tool {call.name!r}")
                run.add_dependency(tool.dependency)

                decision = self.policy.check(tool, call.arguments)
                if decision.allowed and decision.requires_approval:
                    approved = await self._approve(tool, call, decision)
                    if not approved:
                        decision = PermissionDecision.deny(
                            decision.policy, "approval was not granted"
                        )
                if not decision.allowed:
                    run.emit(
                        EventType.TOOL_DENIED,
                        {
                            "tool_call_id": call.id,
                            "name": call.name,
                            "policy": decision.policy,
                            "reason": decision.reason,
                        },
                    )
                    return self._error(run, call, started, f"denied: {decision.reason}")

                substituted = await run.hooks.before_tool_call(call)
                if substituted is not None:
                    run.emit(
                        EventType.REPLAY_SUBSTITUTED,
                        {"kind": "tool", "tool_call_id": call.id, "name": call.name},
                    )
                    return self._finish(
                        run, call, started, substituted.content, substituted.is_error
                    )
                try:
                    value, updates = await self._invoke(run, tool, call)
                except ToolArgumentError as exc:
                    return self._error(run, call, started, str(exc), exc)
                except Exception as exc:
                    return self._error(run, call, started, f"{type(exc).__name__}: {exc}", exc)
                return self._finish(run, call, started, to_jsonable(value), False, updates=updates)

    async def _invoke(self, run: Run, tool: Tool, call: ToolCall) -> tuple[Any, int]:
        """Run the tool, publishing interim updates from a streaming one."""
        if not tool.is_streaming:
            return await tool.ainvoke(**call.arguments), 0
        from rewyn.runtime.streaming import ToolProgress

        value: Any = None
        updates = 0
        async for update in tool.astream(**call.arguments):
            if updates:
                # A further update arrived, so the value we were holding was
                # progress rather than the result. Publish it now.
                run.publish(
                    ToolProgress(
                        tool_call_id=call.id,
                        name=call.name,
                        index=updates - 1,
                        message="" if isinstance(value, dict) else str(value)[:500],
                        data=value if isinstance(value, dict) else None,
                    )
                )
            value = update
            updates += 1
        return value, max(0, updates - 1)

    async def _approve(self, tool: Tool, call: ToolCall, decision: PermissionDecision) -> bool:
        if self.approver is None:
            return False
        return await self.approver(tool, call.arguments, decision)

    def _finish(
        self,
        run: Run,
        call: ToolCall,
        started: float,
        content: Any,
        is_error: bool,
        *,
        updates: int = 0,
    ) -> ToolResult:
        latency = (time.perf_counter() - started) * 1000.0
        run.record_usage(tool_calls=1)
        tool = self.registry.get(call.name)
        cost = tool.price(latency / 1000.0) if tool is not None else 0.0
        if cost:
            run.record_cost("tool", cost)
        run.emit(
            EventType.TOOL_RETURNED,
            {
                "tool_call_id": call.id,
                "name": call.name,
                "result": content,
                "is_error": is_error,
                "latency_ms": latency,
                "cost": cost,
                "progress_updates": updates,
            },
        )
        return ToolResult(tool_call_id=call.id, name=call.name, content=content, is_error=is_error)

    def _error(
        self,
        run: Run,
        call: ToolCall,
        started: float,
        message: str,
        exc: BaseException | None = None,
    ) -> ToolResult:
        result = self._finish(run, call, started, message, True)
        if self.raise_on_error:
            if exc is not None:
                raise exc
            raise ToolExecutionError(message)
        return result


class ToolExecutionError(RuntimeError):
    """Raised for unknown or denied tools when ``raise_on_error`` is set."""
