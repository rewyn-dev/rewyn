"""The execution event model.

Everything in Rewyn becomes an event (spec §42). Events are immutable,
ordered within a run by ``seq``, and carry a JSON payload whose shape is
determined by the event type. This module is the single source of truth for
event names.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.types import JSONObject, new_id, utcnow

EVENT_SCHEMA_VERSION = "1"


class EventType(StrEnum):
    """Canonical event names.

    Names listed in spec §42 are kept verbatim. Additional names follow the
    same ``SUBJECT_VERB`` convention and cover primitives the spec describes
    elsewhere (RAG stages, graphs, state, sandbox, structured outputs).
    """

    # Run lifecycle
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_FAILED = "RUN_FAILED"
    RUN_CANCELLED = "RUN_CANCELLED"

    # Models
    MODEL_CALLED = "MODEL_CALLED"
    MODEL_RESPONSE = "MODEL_RESPONSE"
    OUTPUT_VALIDATED = "OUTPUT_VALIDATED"

    # Context
    CONTEXT_RETRIEVED = "CONTEXT_RETRIEVED"
    CONTEXT_ASSEMBLED = "CONTEXT_ASSEMBLED"

    # Memory
    MEMORY_READ = "MEMORY_READ"
    MEMORY_WRITE = "MEMORY_WRITE"

    # RAG
    DOCUMENT_INDEXED = "DOCUMENT_INDEXED"
    RETRIEVAL_QUERIED = "RETRIEVAL_QUERIED"
    RETRIEVAL_RERANKED = "RETRIEVAL_RERANKED"

    # Skills
    SKILL_LOADED = "SKILL_LOADED"
    SKILL_ACTIVATED = "SKILL_ACTIVATED"

    # MCP
    MCP_CONNECTED = "MCP_CONNECTED"
    MCP_DISCONNECTED = "MCP_DISCONNECTED"
    MCP_TOOLS_DISCOVERED = "MCP_TOOLS_DISCOVERED"

    # Tools
    TOOL_CALLED = "TOOL_CALLED"
    TOOL_RETURNED = "TOOL_RETURNED"
    TOOL_DENIED = "TOOL_DENIED"

    # Agents
    AGENT_LOOP_STARTED = "AGENT_LOOP_STARTED"
    AGENT_LOOP_ITERATION = "AGENT_LOOP_ITERATION"
    AGENT_LOOP_FINISHED = "AGENT_LOOP_FINISHED"
    HANDOFF = "HANDOFF"
    SUBAGENT_STARTED = "SUBAGENT_STARTED"
    SUBAGENT_FINISHED = "SUBAGENT_FINISHED"
    PLAN_CREATED = "PLAN_CREATED"

    # Graphs
    GRAPH_STARTED = "GRAPH_STARTED"
    GRAPH_NODE_STARTED = "GRAPH_NODE_STARTED"
    GRAPH_NODE_FINISHED = "GRAPH_NODE_FINISHED"
    GRAPH_NODE_FAILED = "GRAPH_NODE_FAILED"
    GRAPH_FINISHED = "GRAPH_FINISHED"

    # State / runtime
    STATE_UPDATED = "STATE_UPDATED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    CHECKPOINT_RESTORED = "CHECKPOINT_RESTORED"
    SANDBOX_EXECUTED = "SANDBOX_EXECUTED"

    # Human-in-the-loop
    HUMAN_APPROVAL_REQUESTED = "HUMAN_APPROVAL_REQUESTED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    HUMAN_FEEDBACK = "HUMAN_FEEDBACK"

    # Guardrails
    GUARDRAIL_TRIGGERED = "GUARDRAIL_TRIGGERED"
    GUARDRAIL_PASSED = "GUARDRAIL_PASSED"

    # Evaluation / replay
    EVALUATION_SCORED = "EVALUATION_SCORED"
    REPLAY_SUBSTITUTED = "REPLAY_SUBSTITUTED"


class Event(BaseModel):
    """One immutable execution record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=lambda: new_id("evt"))
    schema_version: str = EVENT_SCHEMA_VERSION
    type: EventType
    run_id: str
    seq: int
    timestamp: datetime = Field(default_factory=utcnow)
    span_id: str | None = None
    parent_span_id: str | None = None
    payload: JSONObject = Field(default_factory=dict)

    def to_record(self) -> JSONObject:
        """JSON-compatible dict suitable for JSONL persistence."""
        return self.model_dump(mode="json")

    @classmethod
    def from_record(cls, record: JSONObject) -> Event:
        """Parse a stored record, upgrading it if it predates this build."""
        from rewyn.core.migrations import migrate

        return cls.model_validate(migrate("event", record))


@runtime_checkable
class EventSink(Protocol):
    """Destination for events. Implementations must never raise from ``emit``."""

    def emit(self, event: Event) -> None: ...

    def flush(self) -> bool | None: ...

    def close(self) -> None: ...


class ListSink:
    """In-memory sink, primarily for tests and inspection."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    def of_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.type is event_type]


def summarize_payload(payload: JSONObject, limit: int = 200) -> dict[str, Any]:
    """Return a shallow, display-safe summary of a payload for CLI output."""
    summary: dict[str, Any] = {}
    for key, value in payload.items():
        text = value if isinstance(value, str) else repr(value)
        summary[key] = text if len(text) <= limit else text[: limit - 1] + "…"
    return summary
