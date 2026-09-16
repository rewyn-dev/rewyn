"""Recording sampling (spec §53: configurable sampling)."""

from __future__ import annotations

import pytest

from rewyn.core.event import Event, EventType
from rewyn.core.run import RunStatus, start_run
from rewyn.runtime.recorder import Recorder
from rewyn.runtime.sampling import (
    ALWAYS_TAG,
    AlwaysSample,
    NeverSample,
    RateSampler,
    always_record,
    is_failure,
    sampler_from_settings,
)
from rewyn.storage.local import LocalStore
from rewyn.testing import FakeModel


def event(kind: EventType, run_id: str = "run_1", seq: int = 1, **payload) -> Event:
    return Event(type=kind, run_id=run_id, seq=seq, payload=payload)


def test_the_default_records_everything():
    assert AlwaysSample().sample("run_1") is True
    assert isinstance(sampler_from_settings(), AlwaysSample)


def test_the_rate_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("REWYN_SAMPLE_RATE", "0.25")
    sampler = sampler_from_settings()
    assert isinstance(sampler, RateSampler)
    assert sampler.rate == 0.25


def test_an_unparseable_rate_keeps_the_default(monkeypatch):
    monkeypatch.setenv("REWYN_SAMPLE_RATE", "loads")
    assert isinstance(sampler_from_settings(), AlwaysSample)


def test_a_rate_outside_the_range_is_clamped(monkeypatch):
    monkeypatch.setenv("REWYN_SAMPLE_RATE", "-3")
    assert sampler_from_settings().sample("run_1") is False
    monkeypatch.setenv("REWYN_SAMPLE_RATE", "9")
    assert isinstance(sampler_from_settings(), AlwaysSample)


def test_an_invalid_rate_is_refused_at_construction():
    with pytest.raises(ValueError, match="between 0 and 1"):
        RateSampler(2.0)


def test_sampling_is_deterministic_for_a_run_id():
    sampler = RateSampler(0.5)
    first = [sampler.sample(f"run_{i}") for i in range(50)]
    second = [RateSampler(0.5).sample(f"run_{i}") for i in range(50)]
    assert first == second, "two processes must agree without coordinating"


def test_the_rate_is_roughly_honoured():
    sampler = RateSampler(0.2)
    kept = sum(sampler.sample(f"run_{i}") for i in range(4000))
    assert 0.15 < kept / 4000 < 0.25


def test_zero_and_one_are_absolute():
    assert RateSampler(0.0).sample("run_1") is False
    assert RateSampler(1.0).sample("run_1") is True
    assert NeverSample().sample("run_1") is False


def test_a_tagged_run_is_never_sampled_out():
    tags = frozenset(always_record(["critical"]))
    assert ALWAYS_TAG in tags
    assert RateSampler(0.0).sample("run_1", tags=tags) is True
    assert NeverSample().sample("run_1", tags=tags) is True
    assert always_record(list(tags)) == list(tags), "tagging twice is a no-op"


def test_failure_events_are_recognised():
    assert is_failure(event(EventType.RUN_FAILED))
    assert is_failure(event(EventType.RUN_CANCELLED))
    assert not is_failure(event(EventType.RUN_FINISHED))


# Recorder integration ----------------------------------------------------------
def recorder(rewyn_home, sampler) -> Recorder:
    store = LocalStore(rewyn_home)
    store.initialize()
    return Recorder(store, sampler=sampler, flush_interval=0.01)


def test_a_sampled_out_run_writes_nothing(rewyn_home):
    rec = recorder(rewyn_home, NeverSample())
    rec.emit(event(EventType.RUN_STARTED, seq=1, name="r"))
    rec.emit(event(EventType.MODEL_CALLED, seq=2))
    rec.emit(event(EventType.RUN_FINISHED, seq=3))
    rec.flush()
    assert rec.sampled_out == 1
    assert not LocalStore(rewyn_home).events_path("run_1").exists()


def test_a_sampled_out_run_that_fails_is_kept_in_full(rewyn_home):
    """Keeping only the failure event would leave a trace nobody can replay."""
    rec = recorder(rewyn_home, NeverSample())
    rec.emit(event(EventType.RUN_STARTED, seq=1, name="r"))
    rec.emit(event(EventType.MODEL_CALLED, seq=2))
    rec.emit(event(EventType.TOOL_CALLED, seq=3))
    rec.emit(event(EventType.RUN_FAILED, seq=4))
    rec.flush()

    written = LocalStore(rewyn_home).read_events("run_1")
    assert [e.seq for e in written] == [1, 2, 3, 4]
    assert rec.sampled_out == 0, "a promoted run is no longer sampled out"


def test_a_run_that_outgrows_the_deferred_buffer_is_dropped(rewyn_home):
    rec = Recorder(LocalStore(rewyn_home), sampler=NeverSample(), max_deferred=3)
    rec.emit(event(EventType.RUN_STARTED, seq=1, name="r"))
    for seq in range(2, 12):
        rec.emit(event(EventType.MODEL_CALLED, seq=seq))
    # Three events fit (RUN_STARTED and two more); the remaining eight are
    # dropped and counted rather than growing memory without a bound.
    assert rec.dropped == 8
    assert len(rec._deferred["run_1"]) == 3


def test_events_for_an_unseen_run_are_kept(rewyn_home):
    """A subagent's run never emits RUN_STARTED through this sink."""
    rec = recorder(rewyn_home, NeverSample())
    rec.emit(event(EventType.MODEL_CALLED, run_id="run_orphan", seq=1))
    rec.flush()
    assert LocalStore(rewyn_home).read_events("run_orphan")


def test_a_tagged_run_survives_a_zero_rate(rewyn_home):
    rec = recorder(rewyn_home, RateSampler(0.0))
    rec.emit(event(EventType.RUN_STARTED, seq=1, name="r", tags=[ALWAYS_TAG]))
    rec.emit(event(EventType.RUN_FINISHED, seq=2))
    rec.flush()
    assert LocalStore(rewyn_home).read_events("run_1")


def test_sampling_end_to_end_through_a_real_run(rewyn_home, monkeypatch):
    from rewyn import Agent

    monkeypatch.setenv("REWYN_SAMPLE_RATE", "0")
    rec = Recorder(LocalStore(rewyn_home), flush_interval=0.01)
    with start_run("sampled", sinks=[rec]) as run:
        Agent(FakeModel(["hi"]), name="s").run("hello")
    rec.flush()
    assert rec.sampled_out >= 1
    assert not LocalStore(rewyn_home).events_path(run.id).exists()


def test_a_failing_run_survives_a_zero_rate_end_to_end(rewyn_home, monkeypatch):
    monkeypatch.setenv("REWYN_SAMPLE_RATE", "0")
    rec = Recorder(LocalStore(rewyn_home), flush_interval=0.01)
    run = start_run("doomed", sinks=[rec])
    with pytest.raises(ValueError, match="boom"), run:
        raise ValueError("boom")
    rec.flush()

    written = LocalStore(rewyn_home).read_events(run.id)
    assert written[0].type is EventType.RUN_STARTED
    assert written[-1].type is EventType.RUN_FAILED
    assert run.status is RunStatus.FAILED
