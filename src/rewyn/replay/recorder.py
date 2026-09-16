"""Reading recorded runs back into replayable artifacts (spec §27).

A recorded run is just its manifest plus its ordered event log. This module
turns that log back into the structured calls a replay needs: the model
requests and responses, and the tool calls and their results. Nothing here
executes anything -- it is the read side that :mod:`rewyn.replay.replay`
and :mod:`rewyn.replay.diff` both consume.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import Event, EventType
from rewyn.core.run import Run, RunManifest
from rewyn.core.types import JSONObject, RewynError
from rewyn.models.base import Cost, Message, ModelResponse, Usage


class ReplayError(RewynError):
    """A run could not be replayed."""


class RecordedModelCall(BaseModel):
    """One ``MODEL_CALLED`` paired with its ``MODEL_RESPONSE``."""

    model_config = ConfigDict(extra="forbid")

    index: int
    seq: int
    request_fingerprint: str | None = None
    provider: str = ""
    model: str = ""
    messages: list[Message] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    request: JSONObject = Field(default_factory=dict)
    response: ModelResponse | None = None
    error: str | None = None
    latency_ms: float = 0.0

    @property
    def text(self) -> str:
        return self.response.text if self.response is not None else ""


class RecordedToolCall(BaseModel):
    """One ``TOOL_CALLED`` paired with its ``TOOL_RETURNED`` (or ``TOOL_DENIED``)."""

    model_config = ConfigDict(extra="forbid")

    index: int
    seq: int
    tool_call_id: str
    name: str
    arguments: JSONObject = Field(default_factory=dict)
    result: Any = None
    is_error: bool = False
    denied: bool = False
    latency_ms: float = 0.0
    version: str | None = None
    fingerprint: str | None = None

    @property
    def key(self) -> str:
        from rewyn.core.schema import canonical_json

        return f"{self.name}:{canonical_json(self.arguments)}"


def response_from_payload(payload: JSONObject) -> ModelResponse | None:
    """Rebuild a :class:`ModelResponse` from a ``MODEL_RESPONSE`` payload."""
    message = payload.get("message")
    if message is None:
        return None
    return ModelResponse(
        id=str(payload.get("response_id") or "resp_replayed"),
        provider=str(payload.get("provider", "")),
        model=str(payload.get("model", "")),
        message=Message.model_validate(message),
        usage=Usage.model_validate(payload.get("usage") or {}),
        cost=Cost.model_validate(payload.get("cost") or {}),
        finish_reason=payload.get("finish_reason") or "stop",
        latency_ms=float(payload.get("latency_ms") or 0.0),
        request_fingerprint=payload.get("request_fingerprint"),
        provider_response_id=payload.get("provider_response_id"),
    )


class RecordedRun:
    """A completed run, read back for replay, diff or evaluation."""

    def __init__(self, manifest: RunManifest, events: Sequence[Event]) -> None:
        self.manifest = manifest
        self.events: list[Event] = sorted(events, key=lambda e: e.seq)
        self.model_calls: list[RecordedModelCall] = []
        self.tool_calls: list[RecordedToolCall] = []
        self._index()

    # Construction ------------------------------------------------------------
    @classmethod
    def load(cls, run_id: str, *, store: Any | None = None) -> RecordedRun:
        """Read a run from local storage (``run_id`` may be a prefix or ``latest``)."""
        from rewyn.storage.local import LocalStore

        resolved_store = store or LocalStore()
        actual = resolved_store.resolve_run_id(run_id)
        return cls(resolved_store.read_manifest(actual), resolved_store.read_events(actual))

    @classmethod
    def from_run(cls, run: Run) -> RecordedRun:
        """Read an in-memory run without touching disk."""
        return cls(run.manifest, run.events)

    # Identity ----------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def input(self) -> Any:
        return self.manifest.input

    @property
    def output(self) -> Any:
        return self.manifest.output

    def events_of(self, *types: EventType) -> list[Event]:
        wanted = set(types)
        return [e for e in self.events if e.type in wanted]

    def __repr__(self) -> str:
        return (
            f"RecordedRun({self.id!r}, events={len(self.events)}, "
            f"model_calls={len(self.model_calls)}, tool_calls={len(self.tool_calls)})"
        )

    # Indexing ----------------------------------------------------------------
    def _index(self) -> None:
        open_models: list[RecordedModelCall] = []
        open_tools: dict[str, RecordedToolCall] = {}
        for event in self.events:
            if event.type is EventType.MODEL_CALLED:
                call = self._model_call(event)
                self.model_calls.append(call)
                open_models.append(call)
            elif event.type is EventType.MODEL_RESPONSE:
                self._attach_response(open_models, event)
            elif event.type is EventType.TOOL_CALLED:
                tool_call = self._tool_call(event)
                self.tool_calls.append(tool_call)
                open_tools[tool_call.tool_call_id] = tool_call
            elif event.type in (EventType.TOOL_RETURNED, EventType.TOOL_DENIED):
                self._attach_tool_result(open_tools, event)

    def _model_call(self, event: Event) -> RecordedModelCall:
        payload = event.payload
        return RecordedModelCall(
            index=len(self.model_calls),
            seq=event.seq,
            request_fingerprint=payload.get("request_fingerprint"),
            provider=str(payload.get("provider", "")),
            model=str(payload.get("model", "")),
            messages=[Message.model_validate(m) for m in payload.get("messages") or []],
            tools=[str(t) for t in payload.get("tools") or []],
            request=dict(payload),
        )

    def _attach_response(self, open_models: list[RecordedModelCall], event: Event) -> None:
        payload = event.payload
        fp = payload.get("request_fingerprint")
        target = next((c for c in open_models if c.request_fingerprint == fp), None)
        if target is None:
            target = open_models[0] if open_models else None
        if target is None:
            return
        target.error = payload.get("error")
        target.latency_ms = float(payload.get("latency_ms") or 0.0)
        target.response = response_from_payload(payload)
        open_models.remove(target)

    def _tool_call(self, event: Event) -> RecordedToolCall:
        payload = event.payload
        return RecordedToolCall(
            index=len(self.tool_calls),
            seq=event.seq,
            tool_call_id=str(payload.get("tool_call_id") or f"call_{event.seq}"),
            name=str(payload.get("name", "")),
            arguments=dict(payload.get("arguments") or {}),
            version=payload.get("version"),
            fingerprint=payload.get("fingerprint"),
        )

    def _attach_tool_result(self, open_tools: dict[str, RecordedToolCall], event: Event) -> None:
        payload = event.payload
        call = open_tools.get(str(payload.get("tool_call_id")))
        if call is None:
            return
        if event.type is EventType.TOOL_DENIED:
            call.denied = True
            return
        call.result = payload.get("result")
        call.is_error = bool(payload.get("is_error"))
        call.latency_ms = float(payload.get("latency_ms") or 0.0)


def load_runs(run_ids: Iterable[str], *, store: Any | None = None) -> list[RecordedRun]:
    return [RecordedRun.load(run_id, store=store) for run_id in run_ids]
