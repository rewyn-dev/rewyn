"""OpenTelemetry export (spec §33).

Observability is a supporting capability, not the product, so Rewyn keeps
its own event log as the source of truth and mirrors it into OpenTelemetry
for teams who already have a collector. Install the extra first::

    pip install 'rewyn[otel]'

Attach the exporter to a run and every Rewyn span becomes an OTel span and
every event becomes a span event::

    with start_run("support") as run:
        OTelExporter().attach(run)

Like the recorder, the exporter fails open: a broken collector must never
take down the host application, so export errors are logged and swallowed.
"""

from __future__ import annotations

import logging
from typing import Any

from rewyn.core.event import Event, EventType
from rewyn.core.run import Run
from rewyn.core.types import JSONObject, MissingDependencyError

log = logging.getLogger("rewyn.otel")

TRACER_NAME = "rewyn"

# Event payload keys worth promoting to first-class OTel attributes.
_METRIC_KEYS = ("provider", "model", "name", "tool_call_id", "agent", "iteration", "node")


def _require_otel() -> Any:
    try:
        from opentelemetry import trace
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise MissingDependencyError("opentelemetry-api", "otel") from exc
    return trace


def _flatten(payload: JSONObject, prefix: str = "rewyn.") -> dict[str, Any]:
    """Flatten a payload into OTel-safe scalar attributes."""
    attributes: dict[str, Any] = {}
    for key, value in payload.items():
        name = f"{prefix}{key}"
        if isinstance(value, bool | int | float | str):
            attributes[name] = value
        elif isinstance(value, dict):
            attributes.update(_flatten(value, f"{name}."))
        elif isinstance(value, list) and all(
            isinstance(item, bool | int | float | str) for item in value
        ):
            attributes[name] = [str(item) for item in value]
        elif value is not None:
            attributes[name] = str(value)[:512]
    return attributes


class OTelExporter:
    """Mirror a run's spans and events into OpenTelemetry.

    The exporter listens to the run's event stream rather than wrapping any
    primitive, so nothing in the SDK needs to know it exists.
    """

    def __init__(self, tracer: Any = None, *, service_name: str = TRACER_NAME) -> None:
        trace = _require_otel()
        self.trace = trace
        self.tracer = tracer or trace.get_tracer(service_name)
        self._spans: dict[str, Any] = {}
        self._contexts: dict[str, Any] = {}
        self._runs: dict[str, Run] = {}

    # Wiring -------------------------------------------------------------------
    def attach(self, run: Run) -> OTelExporter:
        """Start mirroring ``run``.

        Attaching to a run that is already in flight is fine: the span is
        opened immediately and every event emitted so far is replayed onto
        it, so nothing is lost between ``start_run`` and ``attach``.
        """
        from rewyn.core.run import RunStatus

        self._runs[run.id] = run
        run.add_listener(self.emit)
        if run.status is RunStatus.RUNNING and run.id not in self._spans:
            self._open_span(run.id, f"run {run.name}", {})
            for event in run.events:
                self._add_event(event)
        return self

    def detach(self, run: Run) -> None:
        run.remove_listener(self.emit)
        self._runs.pop(run.id, None)
        self._close_all(run.id)

    # EventSink protocol -------------------------------------------------------
    def emit(self, event: Event) -> None:
        try:
            self._emit(event)
        except Exception:  # instrumentation must never break the host app
            log.debug("rewyn otel export failed for %s", event.type, exc_info=True)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        for run_id in list(self._spans):
            self._close_all(run_id)

    # Internals ----------------------------------------------------------------
    def _emit(self, event: Event) -> None:
        if event.type is EventType.RUN_STARTED and event.run_id not in self._spans:
            self._open(event, name=f"run {event.payload.get('name', 'run')}")
            return
        if event.type in (
            EventType.RUN_FINISHED,
            EventType.RUN_FAILED,
            EventType.RUN_CANCELLED,
        ):
            self._add_event(event)
            self._close_all(event.run_id)
            return
        self._add_event(event)

    def _open(self, event: Event, *, name: str) -> None:
        self._open_span(event.run_id, name, _flatten(event.payload))
        self._add_event(event)

    def _open_span(self, run_id: str, name: str, attributes: dict[str, Any]) -> None:
        span = self.tracer.start_span(name, attributes=attributes)
        span.set_attribute("rewyn.run_id", run_id)
        self._spans[run_id] = span
        self._contexts[run_id] = self.trace.set_span_in_context(span)

    def _add_event(self, event: Event) -> None:
        span = self._spans.get(event.run_id)
        if span is None:
            return
        attributes = _flatten(event.payload)
        for key in _METRIC_KEYS:
            if key in event.payload and f"rewyn.{key}" not in attributes:
                attributes[f"rewyn.{key}"] = str(event.payload[key])
        attributes["rewyn.seq"] = event.seq
        if event.span_id:
            attributes["rewyn.span_id"] = event.span_id
        span.add_event(event.type.value, attributes=attributes, timestamp=_nanos(event))
        if event.type is EventType.RUN_FAILED:
            span.set_status(self.trace.Status(self.trace.StatusCode.ERROR))

    def _close_all(self, run_id: str) -> None:
        span = self._spans.pop(run_id, None)
        self._contexts.pop(run_id, None)
        run = self._runs.get(run_id)
        if span is None:
            return
        if run is not None:
            manifest = run.manifest
            span.set_attribute("rewyn.status", manifest.status.value)
            span.set_attribute("rewyn.cost.total", manifest.cost.total)
            span.set_attribute("rewyn.usage.input_tokens", manifest.usage.input_tokens)
            span.set_attribute("rewyn.usage.output_tokens", manifest.usage.output_tokens)
            span.set_attribute("rewyn.usage.model_calls", manifest.usage.model_calls)
            span.set_attribute("rewyn.usage.tool_calls", manifest.usage.tool_calls)
        span.end()


def _nanos(event: Event) -> int:
    return int(event.timestamp.timestamp() * 1_000_000_000)


def export_run(run: Run, tracer: Any = None) -> OTelExporter:
    """Attach a fresh exporter to ``run`` and return it."""
    return OTelExporter(tracer).attach(run)


def otel_available() -> bool:
    """True when the ``otel`` extra is installed."""
    import importlib.util

    return importlib.util.find_spec("opentelemetry") is not None
