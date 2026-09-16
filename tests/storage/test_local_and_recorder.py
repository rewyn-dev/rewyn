from __future__ import annotations

import json
from pathlib import Path

import pytest

from rewyn.core.event import Event, EventType
from rewyn.core.run import RunManifest, RunStatus, start_run
from rewyn.runtime.recorder import Recorder, default_recorder
from rewyn.storage.local import LocalStore, RunNotFoundError


def _event(run_id: str, seq: int, **payload: object) -> Event:
    return Event(type=EventType.TOOL_CALLED, run_id=run_id, seq=seq, payload=payload)


def test_initialize_creates_layout(rewyn_home: Path) -> None:
    store = LocalStore(rewyn_home)
    store.initialize()
    for sub in ("runs", "datasets", "checkpoints", "memory"):
        assert (rewyn_home / sub).is_dir()
    assert json.loads((rewyn_home / "config.json").read_text())["schema_version"] == "1"
    assert (rewyn_home / ".gitignore").read_text() == "*\n"


def test_manifest_and_events_round_trip(rewyn_home: Path) -> None:
    store = LocalStore(rewyn_home)
    manifest = RunManifest(id="run_A", name="demo", status=RunStatus.SUCCEEDED)
    store.write_manifest(manifest)
    store.append_events("run_A", [_event("run_A", 1, a=1), _event("run_A", 2, b=2)])
    store.append_events("run_A", [_event("run_A", 3)])
    assert store.read_manifest("run_A") == manifest
    assert [e.seq for e in store.read_events("run_A")] == [1, 2, 3]
    assert store.read_events("run_A")[0].payload == {"a": 1}


def test_listing_resolution_and_deletion(rewyn_home: Path) -> None:
    store = LocalStore(rewyn_home)
    store.write_manifest(RunManifest(id="run_01AAA", name="first"))
    store.write_manifest(RunManifest(id="run_01BBB", name="second"))
    ids = [m.id for m in store.list_runs()]
    assert set(ids) == {"run_01AAA", "run_01BBB"}
    assert store.resolve_run_id("run_01B") == "run_01BBB"
    assert store.resolve_run_id("latest") in ids
    with pytest.raises(RunNotFoundError):
        store.resolve_run_id("run_01")  # ambiguous
    store.delete_run("run_01AAA")
    with pytest.raises(RunNotFoundError):
        store.read_manifest("run_01AAA")
    with pytest.raises(RunNotFoundError):
        list(store.iter_events("run_01AAA"))


def test_recorder_writes_redacted_events_in_background(rewyn_home: Path) -> None:
    store = LocalStore(rewyn_home)
    recorder = Recorder(store, batch_size=2, flush_interval=0.05)
    for seq in range(1, 6):
        recorder.emit(_event("run_R", seq, secret="sk-ant-api03-abcdefghijklmnopqrstuvwxyz"))
    assert recorder.flush()
    events = store.read_events("run_R")
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert all(e.payload["secret"] == "[REDACTED]" for e in events)
    recorder.close()
    recorder.emit(_event("run_R", 6))  # after close: ignored, never raises
    assert len(store.read_events("run_R")) == 5


def test_recorder_fails_open_when_store_breaks(rewyn_home: Path) -> None:
    class BrokenStore(LocalStore):
        def append_events(self, run_id: str, events: object) -> int:  # type: ignore[override]
            raise OSError("disk full")

    recorder = Recorder(BrokenStore(rewyn_home), flush_interval=0.05)
    recorder.emit(_event("run_X", 1))
    recorder.flush()
    assert recorder.failures >= 1
    recorder.close()


def test_default_recorder_records_runs_end_to_end(rewyn_home: Path) -> None:
    with start_run("recorded", tags=["t"]) as run:
        run.emit(EventType.MODEL_CALLED, {"api_key": "top-secret-value"})
    recorder = default_recorder()
    assert recorder.flush()
    store = LocalStore(rewyn_home)
    manifest = store.read_manifest(run.id)
    assert manifest.status is RunStatus.SUCCEEDED
    assert manifest.tags == ["t"]
    events = store.read_events(run.id)
    assert [e.type for e in events] == [
        EventType.RUN_STARTED,
        EventType.MODEL_CALLED,
        EventType.RUN_FINISHED,
    ]
    assert events[1].payload["api_key"] == "[REDACTED]"


def test_recording_can_be_disabled(rewyn_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REWYN_RECORDING", "off")
    with start_run("silent") as run:
        pass
    assert run.sinks == []
    assert not (rewyn_home / "runs" / run.id).exists()
