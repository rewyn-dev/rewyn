"""Human-in-the-loop approval (spec §26).

::

    decision = await human.approve(action="issue_refund", amount=85000)
    if decision:
        ...

Handlers decide requests; the active handler comes from
``approval_scope(handler)`` or the process default. Every request and its
outcome is recorded (``HUMAN_APPROVAL_REQUESTED``, ``HUMAN_APPROVED`` /
``HUMAN_REJECTED``).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import EventType
from rewyn.core.run import aensure_run
from rewyn.core.span import SpanKind
from rewyn.core.sync import run_sync
from rewyn.core.types import JSONObject, new_id, utcnow
from rewyn.tools.permissions import PermissionDecision
from rewyn.tools.tool import Tool


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=lambda: new_id("apr"))
    action: str
    details: JSONObject = Field(default_factory=dict)
    risk: str = "medium"
    requested_at: datetime = Field(default_factory=utcnow)
    run_id: str | None = None


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    approved: bool
    by: str = "unknown"
    reason: str = ""
    correction: JSONObject | None = None
    decided_at: datetime = Field(default_factory=utcnow)

    def __bool__(self) -> bool:
        return self.approved


class ApprovalHandler(Protocol):
    name: str

    async def decide(self, request: ApprovalRequest) -> ApprovalDecision: ...


class AutoApprove:
    name = "auto_approve"

    def __init__(self, by: str = "auto") -> None:
        self.by = by

    async def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(request_id=request.id, approved=True, by=self.by, reason="auto")


class AutoReject:
    name = "auto_reject"

    def __init__(self, reason: str = "no approval handler configured") -> None:
        self.reason = reason

    async def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(
            request_id=request.id, approved=False, by="system", reason=self.reason
        )


class CallbackHandler:
    """Delegate to ``fn(request) -> bool | ApprovalDecision`` (sync or async)."""

    name = "callback"

    def __init__(
        self,
        fn: Callable[
            [ApprovalRequest], bool | ApprovalDecision | Awaitable[bool | ApprovalDecision]
        ],
        *,
        by: str = "callback",
    ) -> None:
        self.fn = fn
        self.by = by

    async def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        result = self.fn(request)
        if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
            result = await result
        if isinstance(result, ApprovalDecision):
            return result
        return ApprovalDecision(request_id=request.id, approved=bool(result), by=self.by)


class ConsoleHandler:
    """Prompt on the terminal (``y``/``n``); for local development."""

    name = "console"

    def __init__(self, prompt: Callable[[str], str] = input) -> None:
        self.prompt = prompt

    async def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        details = ", ".join(f"{k}={v!r}" for k, v in request.details.items())
        answer = await asyncio.to_thread(
            self.prompt, f"Approve {request.action}({details})? [y/N] "
        )
        approved = answer.strip().lower() in {"y", "yes"}
        return ApprovalDecision(request_id=request.id, approved=approved, by="console")


class QueueHandler:
    """Park requests until something calls :meth:`resolve` (UIs, tests, services)."""

    name = "queue"

    def __init__(self, *, timeout: float | None = None) -> None:
        self.timeout = timeout
        self.pending: dict[str, ApprovalRequest] = {}
        self._futures: dict[str, asyncio.Future[ApprovalDecision]] = {}

    async def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalDecision] = loop.create_future()
        self.pending[request.id] = request
        self._futures[request.id] = future
        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except TimeoutError:
            return ApprovalDecision(
                request_id=request.id, approved=False, by="system", reason="approval timed out"
            )
        finally:
            self.pending.pop(request.id, None)
            self._futures.pop(request.id, None)

    def resolve(
        self,
        request_id: str,
        approved: bool,
        *,
        by: str = "human",
        reason: str = "",
        correction: JSONObject | None = None,
    ) -> None:
        future = self._futures.get(request_id)
        if future is None or future.done():
            raise KeyError(f"no pending approval {request_id!r}")
        future.get_loop().call_soon_threadsafe(
            future.set_result,
            ApprovalDecision(
                request_id=request_id,
                approved=approved,
                by=by,
                reason=reason,
                correction=correction,
            ),
        )


_handler: contextvars.ContextVar[ApprovalHandler | None] = contextvars.ContextVar(
    "rewyn_approval_handler", default=None
)
_default_handler: ApprovalHandler = AutoReject()


def set_default_handler(handler: ApprovalHandler) -> None:
    global _default_handler  # noqa: PLW0603
    _default_handler = handler


def current_handler() -> ApprovalHandler:
    return _handler.get() or _default_handler


@contextlib.contextmanager
def approval_scope(handler: ApprovalHandler) -> Iterator[ApprovalHandler]:
    token = _handler.set(handler)
    try:
        yield handler
    finally:
        _handler.reset(token)


async def approve(
    action: str, *, risk: str = "medium", handler: ApprovalHandler | None = None, **details: Any
) -> ApprovalDecision:
    """Request a human decision for ``action``; the outcome is recorded on the run."""
    handler = handler or current_handler()
    async with aensure_run("human") as run:
        request = ApprovalRequest(action=action, details=details, risk=risk, run_id=run.id)
        with run.span(f"approval:{action}", SpanKind.HUMAN):
            run.emit(
                EventType.HUMAN_APPROVAL_REQUESTED,
                {
                    "request_id": request.id,
                    "action": action,
                    "details": details,
                    "risk": risk,
                    "handler": handler.name,
                },
            )
            decision = await handler.decide(request)
            run.emit(
                EventType.HUMAN_APPROVED if decision.approved else EventType.HUMAN_REJECTED,
                {
                    "request_id": request.id,
                    "action": action,
                    "by": decision.by,
                    "reason": decision.reason,
                    "correction": decision.correction,
                },
            )
            return decision


def approve_sync(action: str, **details: Any) -> ApprovalDecision:
    return run_sync(approve(action, **details))


def tool_approver(handler: ApprovalHandler | None = None) -> Callable[..., Awaitable[bool]]:
    """Adapter so a :class:`ToolExecutor` asks a human before risky tool calls."""

    async def _approver(tool: Tool, arguments: JSONObject, decision: PermissionDecision) -> bool:
        result = await approve(
            f"tool:{tool.name}",
            risk=tool.risk_level.value,
            handler=handler,
            arguments=arguments,
            policy=decision.policy,
            reason=decision.reason,
        )
        return result.approved

    return _approver
