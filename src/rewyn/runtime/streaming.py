"""Unified event streaming (spec §41).

``EventStream`` subscribes to a run and yields every :class:`Event` as it is
emitted plus anything published to the run in between, in order, until the
run finishes. Published items are model token deltas
(:class:`~rewyn.models.base.StreamEvent`) and interim tool updates
(:class:`ToolProgress`), which are the two things that happen too often to
deserve an event each.

The same stream backs ``Agent.astream`` and ``Graph.astream``, so token,
tool, agent lifecycle and graph progress all arrive through one iterator.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any

from pydantic import BaseModel, ConfigDict

from rewyn.core.event import Event, EventType
from rewyn.core.run import Run
from rewyn.core.types import JSONObject
from rewyn.models.base import StreamEvent


class ToolProgress(BaseModel):
    """An interim update from a long-running tool (spec §41: tool streaming).

    Published to the run like a model token delta rather than emitted as an
    event: a tool that reports a hundred times should reach a live view
    without putting a hundred rows in the run log. ``TOOL_RETURNED`` records
    how many updates there were.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = "tool_progress"
    tool_call_id: str
    name: str
    index: int = 0
    message: str = ""
    data: JSONObject | None = None


StreamItem = Event | StreamEvent | ToolProgress

_TERMINAL = {
    EventType.RUN_FINISHED,
    EventType.RUN_FAILED,
    EventType.RUN_CANCELLED,
}

_DONE = object()


class EventStream:
    """Async iterator over a run's events and published deltas."""

    def __init__(self, run: Run, *, stop_on: set[EventType] | None = None) -> None:
        self.run = run
        self.stop_on = stop_on or _TERMINAL
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._loop = asyncio.get_event_loop()
        self._closed = False

    def _put(self, item: Any) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._queue.put_nowait(item)
        else:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    def _on_event(self, event: Event) -> None:
        self._put(event)
        if event.type in self.stop_on:
            self._put(_DONE)

    def _on_delta(self, delta: StreamEvent) -> None:
        self._put(delta)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._put(_DONE)

    def __enter__(self) -> EventStream:
        self.run.add_listener(self._on_event)
        self.run.add_stream_listener(self._on_delta)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.run.remove_listener(self._on_event)
        self.run.remove_stream_listener(self._on_delta)
        self.close()

    def __aiter__(self) -> AsyncIterator[StreamItem]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[StreamItem]:
        while True:
            item = await self._queue.get()
            if item is _DONE:
                return
            yield item


def publish_delta(run: Run | None, delta: StreamEvent) -> None:
    if run is not None:
        run.publish(delta)
