"""OpenTelemetry export. Uses the in-memory span exporter from the otel SDK."""

from __future__ import annotations

import pytest

from rewyn.agents.agent import Agent
from rewyn.core.run import start_run
from rewyn.testing.fake_model import FakeModel
from rewyn.tools.tool import tool

pytest.importorskip("opentelemetry.sdk", reason="needs the 'otel' extra")

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from rewyn.integrations.otel import OTelExporter, otel_available


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider.get_tracer("test")


@tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def test_otel_is_available_in_the_dev_environment():
    assert otel_available()


def test_a_run_becomes_one_span_carrying_its_events(spans):
    exporter, tracer = spans
    from rewyn.core.event import EventType

    with start_run("support", record=False) as run:
        OTelExporter(tracer).attach(run)
        run.emit(EventType.STATE_UPDATED, {"keys": ["answer"]})

    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    span = finished[0]
    assert span.name == "run support"
    assert span.attributes["rewyn.run_id"] == run.id
    assert {e.name for e in span.events} >= {"RUN_STARTED", "STATE_UPDATED", "RUN_FINISHED"}


def test_usage_and_cost_land_on_the_span(spans):
    exporter, tracer = spans
    agent = Agent(FakeModel([FakeModel.tool_call("add", {"a": 1, "b": 2}), "3"]), tools=[add])
    with start_run("agent", record=False) as run:
        OTelExporter(tracer).attach(run)
        agent.run("1+2?")

    span = exporter.get_finished_spans()[0]
    assert span.attributes["rewyn.status"] == "succeeded"
    assert span.attributes["rewyn.usage.model_calls"] == 2
    assert span.attributes["rewyn.usage.tool_calls"] == 1
    assert "rewyn.cost.total" in span.attributes
    names = [e.name for e in span.events]
    assert "MODEL_CALLED" in names
    assert "TOOL_RETURNED" in names


def test_a_failed_run_marks_the_span_as_an_error(spans):
    exporter, tracer = spans
    run = start_run("boom", record=False)
    mirror = OTelExporter(tracer).attach(run)
    with pytest.raises(ValueError, match="boom"), run:
        raise ValueError("boom")
    mirror.close()

    span = exporter.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"


def test_export_failures_never_reach_the_host_app(spans):
    _, tracer = spans

    class Broken(OTelExporter):
        def _emit(self, event):
            raise RuntimeError("collector is down")

    from rewyn.core.event import EventType

    with start_run("resilient", record=False) as run:
        Broken(tracer).attach(run)
        run.emit(EventType.STATE_UPDATED, {"still": "running"})
    assert run.status.value == "succeeded"


def test_export_run_attaches_a_fresh_exporter(spans):
    from rewyn.core.event import EventType
    from rewyn.integrations.otel import export_run

    exporter, tracer = spans
    with start_run("helper", record=False) as run:
        export_run(run, tracer)
        run.emit(EventType.STATE_UPDATED, {"keys": ["a"]})
    span = exporter.get_finished_spans()[0]
    assert span.name == "run helper"


def test_events_for_an_unknown_run_are_ignored(spans):
    from rewyn.core.event import Event, EventType
    from rewyn.integrations.otel import OTelExporter

    _, tracer = spans
    mirror = OTelExporter(tracer)
    orphan = Event(type=EventType.STATE_UPDATED, run_id="run_unknown", seq=1)
    mirror.emit(orphan)  # must not raise
    mirror.close()


def test_the_integrations_subpackage_loads_otel_lazily():
    import rewyn.integrations as integrations

    assert integrations.otel.otel_available()
    with pytest.raises(AttributeError, match="no attribute"):
        _ = integrations.missing


def test_model_and_tool_names_are_promoted_to_span_attributes(spans):
    exporter, tracer = spans
    agent = Agent(FakeModel([FakeModel.tool_call("add", {"a": 1, "b": 2}), "3"]), tools=[add])
    with start_run("promoted", record=False) as run:
        OTelExporter(tracer).attach(run)
        agent.run("1+2?")
    span = exporter.get_finished_spans()[0]
    called = next(e for e in span.events if e.name == "TOOL_CALLED")
    assert called.attributes["rewyn.name"] == "add"
    assert "rewyn.span_id" in called.attributes


def test_flush_and_close_are_safe_to_call(spans):
    _, tracer = spans
    with start_run("closing", record=False) as run:
        mirror = OTelExporter(tracer).attach(run)
        assert mirror.flush() is None
    mirror.close()
    mirror.close()


def test_detaching_stops_the_mirror(spans):
    exporter, tracer = spans
    run = start_run("detach", record=False)
    mirror = OTelExporter(tracer).attach(run)
    with run:
        mirror.detach(run)
    assert len(exporter.get_finished_spans()) == 1
