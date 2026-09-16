"""Spans: hierarchical scopes inside a run.

A span groups the events emitted by one logical unit of work (a model call,
a tool call, an agent iteration, a graph node, a subagent). Spans nest, so a
run's events form a tree that later phases render, diff and replay.
"""

from __future__ import annotations

import contextvars
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.types import JSONObject, new_id, utcnow


class SpanKind(StrEnum):
    RUN = "run"
    MODEL = "model"
    TOOL = "tool"
    AGENT = "agent"
    ITERATION = "iteration"
    CONTEXT = "context"
    MEMORY = "memory"
    RETRIEVAL = "retrieval"
    MCP = "mcp"
    SKILL = "skill"
    GRAPH = "graph"
    NODE = "node"
    SUBAGENT = "subagent"
    HANDOFF = "handoff"
    SANDBOX = "sandbox"
    GUARDRAIL = "guardrail"
    HUMAN = "human"
    EVALUATION = "evaluation"
    CUSTOM = "custom"


class SpanStatus(StrEnum):
    OPEN = "open"
    OK = "ok"
    ERROR = "error"


class Span(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("span"))
    run_id: str
    name: str
    kind: SpanKind = SpanKind.CUSTOM
    parent_id: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    status: SpanStatus = SpanStatus.OPEN
    error: str | None = None
    attributes: JSONObject = Field(default_factory=dict)

    @property
    def duration_ms(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds() * 1000.0

    def end(self, *, error: str | None = None) -> None:
        self.ended_at = utcnow()
        self.status = SpanStatus.ERROR if error else SpanStatus.OK
        self.error = error


_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "rewyn_current_span", default=None
)


def current_span() -> Span | None:
    return _current_span.get()


def set_current_span(span: Span | None) -> contextvars.Token[Span | None]:
    return _current_span.set(span)


def reset_current_span(token: contextvars.Token[Span | None]) -> None:
    _current_span.reset(token)
