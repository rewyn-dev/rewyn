"""Handoffs (spec §19): explicit transfer of a conversation between agents.

A handoff records sender, receiver, the context and state transferred, the
reason and the time. The receiving agent continues with the transferred
transcript; its answer becomes the run's output.
"""

from __future__ import annotations

import contextvars
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import EventType
from rewyn.core.run import aensure_run
from rewyn.core.span import SpanKind
from rewyn.core.state import State
from rewyn.core.types import JSONObject, new_id, utcnow
from rewyn.models.base import Message, Role
from rewyn.tools.tool import Tool, make_tool

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.agents.agent import Agent, RunResult
    from rewyn.agents.loop import LoopContext


class Handoff(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=lambda: new_id("handoff"))
    sender: str
    receiver: str
    reason: str
    summary: str | None = None
    messages_transferred: int = 0
    state_version: int | None = None
    state_fingerprint: str | None = None
    timestamp: datetime = Field(default_factory=utcnow)


_current_loop: contextvars.ContextVar[LoopContext | None] = contextvars.ContextVar(
    "rewyn_loop_context", default=None
)


def current_loop_context() -> LoopContext | None:
    return _current_loop.get()


def set_loop_context(ctx: LoopContext | None) -> contextvars.Token[LoopContext | None]:
    return _current_loop.set(ctx)


def reset_loop_context(token: contextvars.Token[LoopContext | None]) -> None:
    _current_loop.reset(token)


async def handoff(
    sender: Agent | str,
    receiver: Agent,
    *,
    messages: Sequence[Message] = (),
    reason: str = "",
    summary: str | None = None,
    state: State | None = None,
) -> RunResult:
    """Transfer the conversation to ``receiver`` and run it. Records ``HANDOFF``."""
    sender_name = sender if isinstance(sender, str) else sender.name
    transferred = [m for m in messages if m.role is not Role.SYSTEM]
    record = Handoff(
        sender=sender_name,
        receiver=receiver.name,
        reason=reason,
        summary=summary,
        messages_transferred=len(transferred),
        state_version=state.version if state is not None else None,
        state_fingerprint=state.snapshot().fingerprint if state is not None else None,
    )
    async with aensure_run(receiver.name) as run:
        with run.span(f"handoff:{sender_name}->{receiver.name}", SpanKind.HANDOFF):
            run.emit(EventType.HANDOFF, record.model_dump(mode="json"))
            input_messages: list[Message] = list(transferred)
            if summary:
                input_messages.append(
                    Message.user(f"Handoff from {sender_name}: {reason}\n\nSummary: {summary}")
                )
            elif not input_messages:
                input_messages.append(Message.user(f"Handoff from {sender_name}: {reason}"))
            return await receiver.arun(input_messages, state=state)


def handoff_tool(sender: Agent, receiver: Agent) -> Tool:
    """A ``handoff_to_<name>(reason, summary)`` tool that ends the sender's loop."""
    tool_name = f"handoff_to_{_slug(receiver.name)}"

    async def transfer(reason: str, summary: str = "") -> str:
        ctx = current_loop_context()
        messages = list(ctx.messages) if ctx is not None else []
        state = ctx.state if ctx is not None else None
        result = await handoff(
            sender, receiver, messages=messages, reason=reason, summary=summary or None, state=state
        )
        if ctx is not None:
            ctx.scratch["handoff"] = {"receiver": receiver.name, "output": result.output}
            ctx.output = result.output
            ctx.structured = result.structured
        return result.output

    return make_tool(
        transfer,
        name=tool_name,
        description=(
            f"Hand the conversation over to the {receiver.name!r} agent when it is better "
            "suited to continue. Give the reason and a short summary of progress so far."
        ),
        tags=["handoff"],
    )


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower() or "agent"


def describe(record: Handoff) -> JSONObject:
    return dict(record.model_dump(mode="json"))


__all__ = [
    "Handoff",
    "current_loop_context",
    "describe",
    "handoff",
    "handoff_tool",
    "reset_loop_context",
    "set_loop_context",
]


def _unused(_: Any) -> None:  # pragma: no cover
    return None
