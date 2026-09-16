from __future__ import annotations

from datetime import UTC, datetime

from rewyn.core.state import State


def test_state_versions_every_mutation() -> None:
    state = State({"status": "pending"})
    assert state.version == 0
    state["customer"] = "acme"
    state.update(status="active", task="review")
    del state["task"]
    assert state.version == 3
    assert dict(state) == {"status": "active", "customer": "acme"}
    assert [s.reason for s in state.history] == ["initial", "set customer", "update", "delete task"]


def test_snapshots_are_immutable_and_restorable() -> None:
    state = State({"n": 1})
    snap = state.snapshot("checkpoint")
    state["n"] = 2
    assert snap.data == {"n": 1}
    assert snap.fingerprint != state.snapshot().fingerprint
    state.restore(snap)
    assert state["n"] == 1
    assert state.version == 2
    assert state.history[-1].reason == "restore v0"


def test_snapshot_serialises_nested_objects() -> None:
    state = State({"when": datetime(2026, 1, 1, tzinfo=UTC)})
    assert state.to_dict() == {"when": "2026-01-01T00:00:00Z"}
