"""Schema migration utilities (spec §58)."""

from __future__ import annotations

import json

import pytest

from rewyn.core.event import EVENT_SCHEMA_VERSION, Event, EventType
from rewyn.core.migrations import (
    MigrationError,
    clear,
    current_version,
    migrate,
    migration,
    path,
    register,
    registered,
)


@pytest.fixture(autouse=True)
def clean_registry():
    clear()
    yield
    clear()


def test_every_persisted_schema_declares_a_version():
    for schema in ("run", "event", "skill", "manifest", "dataset", "report", "bundle"):
        assert current_version(schema) == "1", schema


def test_a_record_at_the_current_version_is_untouched():
    record = {"schema_version": "1", "type": "RUN_STARTED"}
    assert migrate("event", record) is record


def test_a_record_is_carried_forward_one_step_at_a_time():
    @migration("event", "1")
    def to_two(record):
        return {**record, "severity": "info"}

    @migration("event", "2")
    def to_three(record):
        return {**record, "channel": "default"}

    upgraded = migrate("event", {"schema_version": "1", "type": "X"}, to_version="3")
    assert upgraded["severity"] == "info"
    assert upgraded["channel"] == "default"
    assert upgraded["schema_version"] == "3"
    assert to_two is not None
    assert to_three is not None


def test_a_missing_version_is_treated_as_one():
    @migration("event", "1")
    def to_two(record):
        return {**record, "added": True}

    assert migrate("event", {"type": "X"}, to_version="2")["added"] is True
    assert to_two is not None


def test_a_gap_in_the_chain_is_an_error():
    with pytest.raises(MigrationError, match="no migration from event v1"):
        migrate("event", {"schema_version": "1"}, to_version="2")


def test_a_record_from_a_newer_build_is_left_alone():
    """Mangling a record we do not understand is worse than not understanding it."""
    record = {"schema_version": "99", "type": "X", "unknown_field": 1}
    assert migrate("event", record) == record


def test_the_migration_path_is_reported():
    register("run", "1", "2", lambda r: r)
    register("run", "2", "3", lambda r: r)
    assert path("run", "1", "3") == ["1", "2", "3"]
    assert path("run", "3", "3") == ["3"]


def test_registering_twice_from_one_version_is_refused():
    register("run", "1", "2", lambda r: r)
    with pytest.raises(MigrationError, match="already registered"):
        register("run", "1", "3", lambda r: r)


def test_a_migration_must_advance_the_version():
    with pytest.raises(MigrationError, match="does not advance"):
        register("run", "1", "1", lambda r: r)


def test_the_registry_can_be_listed():
    register("run", "1", "2", lambda r: r)
    register("event", "1", "2", lambda r: r)
    assert registered() == {"event": ["1->2"], "run": ["1->2"]}


def test_an_event_from_an_older_build_is_upgraded_when_it_is_read_back():
    @migration("event", "0", "1")
    def fill_payload(record):
        return {**record, "payload": {"recovered": True}}

    stored = {
        "id": "evt_1",
        "schema_version": "0",
        "type": "RUN_STARTED",
        "run_id": "run_1",
        "seq": 1,
        "timestamp": "2026-01-01T00:00:00Z",
    }
    event = Event.from_record(stored)
    assert event.payload == {"recovered": True}
    assert event.schema_version == "1"
    assert fill_payload is not None


def test_a_stored_manifest_from_an_older_build_still_loads(rewyn_home):
    from rewyn.storage.local import LocalStore, atomic_write_text

    @migration("run", "0", "1")
    def fill_project(record):
        return {**record, "project": "recovered"}

    store = LocalStore(rewyn_home)
    store.initialize()
    record = {
        "id": "run_old",
        "name": "legacy",
        "schema_version": "0",
        "started_at": "2026-01-01T00:00:00Z",
    }
    atomic_write_text(store.manifest_path("run_old"), json.dumps(record))

    manifest = store.read_manifest("run_old")
    assert manifest.project == "recovered"
    assert manifest.schema_version == "1"
    assert fill_project is not None


def test_a_stored_dataset_from_an_older_build_still_loads(rewyn_home):
    from rewyn.evaluation.dataset import Dataset
    from rewyn.storage.local import atomic_write_text

    @migration("dataset", "0", "1")
    def fill_description(record):
        return {**record, "description": "recovered"}

    atomic_write_text(
        Dataset.path_for("legacy"),
        json.dumps({"name": "legacy", "schema_version": "0", "items": []}),
    )
    assert Dataset.load("legacy").description == "recovered"
    assert fill_description is not None


def test_the_event_schema_version_is_stamped_on_new_events():
    event = Event(type=EventType.RUN_STARTED, run_id="run_1", seq=1)
    assert event.schema_version == EVENT_SCHEMA_VERSION
