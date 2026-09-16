"""Runs: the unit of execution that every event belongs to.

A ``Run`` owns the event sequence, the span tree, a manifest describing the
run and its dependencies, and accumulated usage/cost. Primitives find the
active run through ``current_run()`` (a context variable) and emit events
into it. When no run is active, ``ensure_run()`` starts an implicit one so a
bare ``model.generate()`` is still recorded.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
import traceback
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from datetime import datetime
from enum import StrEnum
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import Event, EventSink, EventType
from rewyn.core.span import (
    Span,
    SpanKind,
    current_span,
    reset_current_span,
    set_current_span,
)
from rewyn.core.types import JSONObject, new_id, utcnow

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.models.base import ModelRequest, ModelResponse
    from rewyn.tools.tool import ToolCall, ToolResult

RUN_SCHEMA_VERSION = "1"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UsageTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class CostTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: float = 0.0
    tool: float = 0.0
    embedding: float = 0.0
    retrieval: float = 0.0
    sandbox: float = 0.0
    currency: str = "USD"

    @property
    def total(self) -> float:
        return self.model + self.tool + self.embedding + self.retrieval + self.sandbox


class DependencyRef(BaseModel):
    """One AI dependency used by a run (spec §34)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    name: str
    version: str = "unversioned"
    fingerprint: str | None = None
    metadata: JSONObject = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


class RunManifest(BaseModel):
    """Everything needed to identify and reconstruct a run."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    project: str = "default"
    status: RunStatus = RunStatus.PENDING
    schema_version: str = RUN_SCHEMA_VERSION
    sdk_version: str = "0.0.0"
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    parent_run_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: JSONObject = Field(default_factory=dict)
    dependencies: list[DependencyRef] = Field(default_factory=list)
    usage: UsageTotals = Field(default_factory=UsageTotals)
    cost: CostTotals = Field(default_factory=CostTotals)
    event_count: int = 0
    error: str | None = None
    input: Any = None
    output: Any = None

    @property
    def duration_ms(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds() * 1000.0


class RunHooks(Protocol):
    """Interception points used by replay (phase 4) and tests.

    Returning a value from a ``before_*`` hook substitutes it for the real
    call. Returning ``None`` lets the real call proceed.
    """

    async def before_model_call(self, request: ModelRequest) -> ModelResponse | None: ...

    async def before_tool_call(self, call: ToolCall) -> ToolResult | None: ...


class NoHooks:
    async def before_model_call(self, request: ModelRequest) -> ModelResponse | None:
        return None

    async def before_tool_call(self, call: ToolCall) -> ToolResult | None:
        return None


class Run:
    """An in-progress or completed execution."""

    def __init__(
        self,
        *,
        name: str = "run",
        sinks: Sequence[EventSink] = (),
        run_id: str | None = None,
        project: str | None = None,
        parent_run_id: str | None = None,
        tags: Sequence[str] = (),
        metadata: JSONObject | None = None,
        hooks: RunHooks | None = None,
        keep_events: bool = True,
        input: Any = None,
    ) -> None:
        from rewyn import __version__
        from rewyn.core.settings import get_settings

        settings = get_settings()
        # UI §42: every run carries its environment. It is a reserved metadata
        # key rather than a manifest field (decision D-3), and an explicit
        # value always wins over the process default -- a run that says which
        # environment it belongs to knows better than the shell does.
        run_metadata = dict(metadata or {})
        run_metadata.setdefault("environment", settings.environment)
        self.manifest = RunManifest(
            id=run_id or new_id("run"),
            name=name,
            project=project or settings.project,
            sdk_version=__version__,
            parent_run_id=parent_run_id,
            tags=list(tags),
            metadata=run_metadata,
        )
        self._sinks: list[EventSink] = list(sinks)
        self._events: list[Event] = []
        self._spans: dict[str, Span] = {}
        self._seq = 0
        self._lock = threading.Lock()
        self._keep_events = keep_events
        self.hooks: RunHooks = hooks or NoHooks()
        self._token: contextvars.Token[Run | None] | None = None
        self._root_span: Span | None = None
        self._root_span_token: contextvars.Token[Span | None] | None = None
        self._listeners: list[Callable[[Event], None]] = []
        self._stream_listeners: list[Callable[[Any], None]] = []
        self._initial_input = input

    # Identity ----------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def status(self) -> RunStatus:
        return self.manifest.status

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    @property
    def spans(self) -> list[Span]:
        return list(self._spans.values())

    @property
    def sinks(self) -> list[EventSink]:
        return list(self._sinks)

    def add_sink(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    def add_listener(self, listener: Callable[[Event], None]) -> None:
        """Register an in-process callback invoked for every event (streaming UIs)."""
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[Event], None]) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def add_stream_listener(self, listener: Callable[[Any], None]) -> None:
        """Register a callback for published non-event items (token deltas)."""
        self._stream_listeners.append(listener)

    def remove_stream_listener(self, listener: Callable[[Any], None]) -> None:
        with contextlib.suppress(ValueError):
            self._stream_listeners.remove(listener)

    def publish(self, item: Any) -> None:
        """Deliver a non-event item (for example a model text delta) to stream listeners."""
        for listener in self._stream_listeners:
            with contextlib.suppress(Exception):
                listener(item)

    # Events ------------------------------------------------------------------
    def emit(
        self,
        event_type: EventType,
        payload: JSONObject | None = None,
        *,
        span: Span | None = None,
    ) -> Event:
        """Create, store and dispatch an event. Never raises for sink failures."""
        active_span = span or current_span()
        with self._lock:
            self._seq += 1
            event = Event(
                type=event_type,
                run_id=self.id,
                seq=self._seq,
                span_id=active_span.id if active_span else None,
                parent_span_id=active_span.parent_id if active_span else None,
                payload=payload or {},
            )
            if self._keep_events:
                self._events.append(event)
            self.manifest.event_count = self._seq
        for sink in self._sinks:
            with contextlib.suppress(Exception):
                sink.emit(event)
        for listener in self._listeners:
            with contextlib.suppress(Exception):
                listener(event)
        return event

    def events_of(self, event_type: EventType) -> list[Event]:
        return [e for e in self._events if e.type is event_type]

    # Spans -------------------------------------------------------------------
    @contextlib.contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind = SpanKind.CUSTOM,
        **attributes: Any,
    ) -> Iterator[Span]:
        """Open a child span of the current span for the duration of the block."""
        parent = current_span()
        span = Span(
            run_id=self.id,
            name=name,
            kind=kind,
            parent_id=parent.id if parent else None,
            attributes=attributes,
        )
        with self._lock:
            self._spans[span.id] = span
        token = set_current_span(span)
        try:
            yield span
        except BaseException as exc:
            span.end(error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            if span.status is not span.status.ERROR:
                span.end()
        finally:
            reset_current_span(token)

    # Accounting --------------------------------------------------------------
    def record_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        model_calls: int = 0,
        tool_calls: int = 0,
    ) -> None:
        with self._lock:
            usage = self.manifest.usage
            usage.input_tokens += input_tokens
            usage.output_tokens += output_tokens
            usage.cache_read_tokens += cache_read_tokens
            usage.cache_write_tokens += cache_write_tokens
            usage.reasoning_tokens += reasoning_tokens
            usage.model_calls += model_calls
            usage.tool_calls += tool_calls

    def record_cost(self, category: str, amount: float) -> None:
        with self._lock:
            cost = self.manifest.cost
            setattr(cost, category, getattr(cost, category) + amount)

    def add_dependency(self, dependency: DependencyRef) -> None:
        with self._lock:
            existing = {d.key: i for i, d in enumerate(self.manifest.dependencies)}
            index = existing.get(dependency.key)
            if index is None:
                self.manifest.dependencies.append(dependency)
            else:
                self.manifest.dependencies[index] = dependency

    # Lifecycle ---------------------------------------------------------------
    def start(self, input: Any = None) -> None:
        """Begin the run. ``input`` defaults to whatever ``start_run`` was given."""
        from rewyn.core.schema import to_jsonable

        if self.manifest.status is not RunStatus.PENDING:
            return
        resolved = self._initial_input if input is None else input
        self.manifest.status = RunStatus.RUNNING
        self.manifest.started_at = utcnow()
        self.manifest.input = to_jsonable(resolved) if resolved is not None else None
        self._token = _current_run.set(self)
        self._root_span = Span(run_id=self.id, name=self.name, kind=SpanKind.RUN)
        self._spans[self._root_span.id] = self._root_span
        self._root_span_token = set_current_span(self._root_span)
        self.emit(
            EventType.RUN_STARTED,
            {
                "name": self.name,
                "project": self.manifest.project,
                "parent_run_id": self.manifest.parent_run_id,
                "tags": self.manifest.tags,
                "input": self.manifest.input,
            },
        )

    def finish(
        self,
        *,
        status: RunStatus = RunStatus.SUCCEEDED,
        error: BaseException | str | None = None,
        output: Any = None,
    ) -> None:
        if self.manifest.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
            return
        error_text = None
        if isinstance(error, BaseException):
            error_text = f"{type(error).__name__}: {error}"
        elif error:
            error_text = str(error)
        self.manifest.status = status
        self.manifest.ended_at = utcnow()
        self.manifest.error = error_text
        if output is not None:
            self.manifest.output = output
        payload: JSONObject = {
            "status": status.value,
            "usage": self.manifest.usage.model_dump(),
            "cost": self.manifest.cost.model_dump() | {"total": self.manifest.cost.total},
            "duration_ms": self.manifest.duration_ms,
            "output": self.manifest.output,
        }
        if status is RunStatus.FAILED:
            payload["error"] = error_text
            if isinstance(error, BaseException):
                payload["traceback"] = "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                )
            self.emit(EventType.RUN_FAILED, payload)
        elif status is RunStatus.CANCELLED:
            self.emit(EventType.RUN_CANCELLED, payload)
        else:
            self.emit(EventType.RUN_FINISHED, payload)
        if self._root_span is not None:
            self._root_span.end(error=error_text)
        if self._root_span_token is not None:
            with contextlib.suppress(ValueError):
                reset_current_span(self._root_span_token)
            self._root_span_token = None
        if self._token is not None:
            with contextlib.suppress(ValueError):
                _current_run.reset(self._token)
            self._token = None
        for sink in self._sinks:
            with contextlib.suppress(Exception):
                sink.flush()

    # Context manager support -------------------------------------------------
    def __enter__(self) -> Run:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None:
            self.finish(status=RunStatus.FAILED, error=exc)
        else:
            self.finish()

    async def __aenter__(self) -> Run:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, tb)


_current_run: contextvars.ContextVar[Run | None] = contextvars.ContextVar(
    "rewyn_current_run", default=None
)


def current_run() -> Run | None:
    """The run active in this context, if any."""
    return _current_run.get()


def _default_sinks() -> list[EventSink]:
    from rewyn.core.settings import get_settings

    if not get_settings().recording:
        return []
    from rewyn.runtime.recorder import default_recorder

    return [default_recorder()]


def start_run(
    name: str = "run",
    *,
    sinks: Sequence[EventSink] | None = None,
    record: bool = True,
    tags: Sequence[str] = (),
    metadata: JSONObject | None = None,
    hooks: RunHooks | None = None,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    input: Any = None,
) -> Run:
    """Create a run. Use as ``with start_run("name") as run:`` or ``async with``.

    ``sinks`` defaults to the local recorder (unless recording is disabled by
    settings or ``record=False``). The returned run is not started until the
    context is entered; ``input`` is recorded on the manifest when it is.
    """
    resolved_sinks = list(sinks) if sinks is not None else (_default_sinks() if record else [])
    return Run(
        name=name,
        sinks=resolved_sinks,
        tags=tags,
        metadata=metadata,
        hooks=hooks,
        run_id=run_id,
        parent_run_id=parent_run_id,
        input=input,
    )


@contextlib.contextmanager
def ensure_run(name: str) -> Iterator[Run]:
    """Yield the active run, or start (and finish) an implicit one."""
    active = current_run()
    if active is not None:
        yield active
        return
    with start_run(name) as run:
        yield run


@contextlib.asynccontextmanager
async def aensure_run(name: str) -> AsyncIterator[Run]:
    active = current_run()
    if active is not None:
        yield active
        return
    async with start_run(name) as run:
        yield run
