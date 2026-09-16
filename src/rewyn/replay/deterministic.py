"""Deterministic replay: recorded responses substituted for live calls (spec §27).

:class:`ReplayHooks` implements the :class:`~rewyn.core.run.RunHooks`
protocol that every model and tool call already consults. When a component
is in ``recorded`` mode the hook returns the recorded response and the real
provider or tool function is never invoked; the call still emits its normal
``MODEL_CALLED``/``TOOL_CALLED`` events plus a ``REPLAY_SUBSTITUTED`` marker,
so a replayed run is observable exactly like a live one.

Matching is by content first and position second. A model request is matched
on its fingerprint; if the fingerprint is not in the recording (the prompt
changed) the next unused recorded call is used instead and a
:class:`Mismatch` is recorded. ``strict=True`` turns mismatches into errors.
"""

from __future__ import annotations

from collections import deque
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from rewyn.core.schema import canonical_json
from rewyn.core.types import JSONObject
from rewyn.models.base import ModelRequest, ModelResponse
from rewyn.replay.recorder import (
    RecordedModelCall,
    RecordedRun,
    RecordedToolCall,
    ReplayError,
)
from rewyn.tools.tool import ToolCall, ToolResult


class ComponentMode(StrEnum):
    """How one component behaves during a replay."""

    RECORDED = "recorded"
    """Return the recorded response; never call the provider or tool."""

    LIVE = "live"
    """Execute for real."""


MatchKind = Literal["fingerprint", "arguments", "position", "exhausted"]


class Mismatch(BaseModel):
    """A replayed call that did not line up with the recording."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["model", "tool"]
    reason: Literal["not_recorded", "content_changed"]
    index: int
    expected: str | None = None
    actual: str | None = None
    detail: str = ""


class Substitution(BaseModel):
    """A call answered from the recording."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["model", "tool"]
    index: int
    matched_by: MatchKind
    name: str = ""


class ReplayExhaustedError(ReplayError):
    """The recording ran out of responses for a component in ``recorded`` mode."""


class ReplayMismatchError(ReplayError):
    """A strict replay diverged from the recording."""

    def __init__(self, mismatch: Mismatch) -> None:
        super().__init__(
            f"replay diverged on {mismatch.kind} call {mismatch.index}: {mismatch.detail}"
        )
        self.mismatch = mismatch


class ReplayHooks:
    """Run hooks that answer model and tool calls from a recording."""

    def __init__(
        self,
        recorded: RecordedRun,
        *,
        model: ComponentMode = ComponentMode.RECORDED,
        tools: ComponentMode = ComponentMode.RECORDED,
        strict: bool = False,
    ) -> None:
        self.recorded = recorded
        self.model_mode = ComponentMode(model)
        self.tool_mode = ComponentMode(tools)
        self.strict = strict
        self.substitutions: list[Substitution] = []
        self.mismatches: list[Mismatch] = []
        self._model_queue: deque[RecordedModelCall] = deque(
            c for c in recorded.model_calls if c.response is not None
        )
        self._model_by_fingerprint: dict[str, deque[RecordedModelCall]] = {}
        for call in self._model_queue:
            if call.request_fingerprint:
                self._model_by_fingerprint.setdefault(call.request_fingerprint, deque()).append(
                    call
                )
        self._tool_queue: deque[RecordedToolCall] = deque(recorded.tool_calls)
        self._tool_by_key: dict[str, deque[RecordedToolCall]] = {}
        for tool_call in self._tool_queue:
            self._tool_by_key.setdefault(tool_call.key, deque()).append(tool_call)
        self._model_index = 0
        self._tool_index = 0

    # Counters ----------------------------------------------------------------
    @property
    def remaining_model_calls(self) -> int:
        return len(self._model_queue)

    @property
    def remaining_tool_calls(self) -> int:
        return len(self._tool_queue)

    @property
    def diverged(self) -> bool:
        return bool(self.mismatches)

    # RunHooks protocol -------------------------------------------------------
    async def before_model_call(self, request: ModelRequest) -> ModelResponse | None:
        if self.model_mode is ComponentMode.LIVE:
            return None
        index = self._model_index
        self._model_index += 1
        call = self._take_model(request, index)
        if call is None or call.response is None:
            return None
        response = call.response.model_copy(deep=True)
        response.request_fingerprint = request.fingerprint()
        return response

    async def before_tool_call(self, call: ToolCall) -> ToolResult | None:
        if self.tool_mode is ComponentMode.LIVE:
            return None
        index = self._tool_index
        self._tool_index += 1
        recorded = self._take_tool(call, index)
        if recorded is None:
            return None
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            content=recorded.result,
            is_error=recorded.is_error,
        )

    # Matching ----------------------------------------------------------------
    def _take_model(self, request: ModelRequest, index: int) -> RecordedModelCall | None:
        fingerprint = request.fingerprint()
        queued = self._model_by_fingerprint.get(fingerprint)
        if queued:
            call = queued.popleft()
            self._model_queue.remove(call)
            self._substituted("model", index, "fingerprint", request.model)
            return call
        if not self._model_queue:
            self._exhausted("model", index, f"no recorded response for {request.model}")
            return None
        call = self._model_queue.popleft()
        if call.request_fingerprint:
            bucket = self._model_by_fingerprint.get(call.request_fingerprint)
            if bucket and call in bucket:
                bucket.remove(call)
        self._record_mismatch(
            Mismatch(
                kind="model",
                reason="content_changed",
                index=index,
                expected=call.request_fingerprint,
                actual=fingerprint,
                detail="request fingerprint differs from the recording; matched by position",
            )
        )
        self._substituted("model", index, "position", request.model)
        return call

    def _take_tool(self, call: ToolCall, index: int) -> RecordedToolCall | None:
        key = f"{call.name}:{canonical_json(call.arguments)}"
        queued = self._tool_by_key.get(key)
        if queued:
            recorded = queued.popleft()
            self._tool_queue.remove(recorded)
            self._substituted("tool", index, "arguments", call.name)
            return recorded
        by_name = [c for c in self._tool_queue if c.name == call.name]
        if not by_name:
            self._exhausted("tool", index, f"no recorded result for tool {call.name!r}")
            return None
        recorded = by_name[0]
        self._tool_queue.remove(recorded)
        bucket = self._tool_by_key.get(recorded.key)
        if bucket and recorded in bucket:
            bucket.remove(recorded)
        self._record_mismatch(
            Mismatch(
                kind="tool",
                reason="content_changed",
                index=index,
                expected=canonical_json(recorded.arguments),
                actual=canonical_json(call.arguments),
                detail=f"arguments for tool {call.name!r} differ; matched by name and position",
            )
        )
        self._substituted("tool", index, "position", call.name)
        return recorded

    def _exhausted(self, kind: Literal["model", "tool"], index: int, detail: str) -> None:
        self.mismatches.append(
            Mismatch(kind=kind, reason="not_recorded", index=index, detail=detail)
        )
        if self.strict:
            raise ReplayExhaustedError(detail)

    def _record_mismatch(self, mismatch: Mismatch) -> None:
        self.mismatches.append(mismatch)
        if self.strict:
            raise ReplayMismatchError(mismatch)

    def _substituted(
        self, kind: Literal["model", "tool"], index: int, matched_by: MatchKind, name: str
    ) -> None:
        self.substitutions.append(
            Substitution(kind=kind, index=index, matched_by=matched_by, name=name)
        )

    def report(self) -> JSONObject:
        return {
            "model_mode": self.model_mode.value,
            "tool_mode": self.tool_mode.value,
            "substitutions": len(self.substitutions),
            "mismatches": [m.model_dump(mode="json") for m in self.mismatches],
            "unused_model_calls": len(self._model_queue),
            "unused_tool_calls": len(self._tool_queue),
        }


class NoSubstitution:
    """Hooks that never substitute: a fully live re-execution."""

    async def before_model_call(self, request: ModelRequest) -> ModelResponse | None:
        return None

    async def before_tool_call(self, call: ToolCall) -> ToolResult | None:
        return None
