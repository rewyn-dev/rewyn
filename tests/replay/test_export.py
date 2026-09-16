from __future__ import annotations

import json
from pathlib import Path

import pytest

from rewyn import Agent, tool
from rewyn.replay.export import BundleError, export_run, import_run
from rewyn.runtime.recorder import default_recorder
from rewyn.storage.local import LocalStore
from rewyn.testing import FakeModel


@tool
def ping() -> str:
    """Ping."""
    return "pong"


def _recorded_run() -> str:
    model = FakeModel([FakeModel.tool_call("ping"), "done"])
    result = Agent(model=model, tools=[ping]).run("go")
    default_recorder().flush()
    return result.run_id


def test_export_bundle_layout(tmp_path: Path) -> None:
    run_id = _recorded_run()
    bundle = export_run(run_id, tmp_path / "out")
    assert bundle == tmp_path / "out" / run_id
    assert (bundle / "manifest.json").exists()
    assert (bundle / "events.jsonl").exists()
    meta = json.loads((bundle / "metadata.json").read_text())
    assert meta["run_id"] == run_id
    assert meta["event_count"] == len(LocalStore().read_events(run_id))
    assert len(list((bundle / "prompts").glob("*_model_called.json"))) == 2
    assert len(list((bundle / "tools").glob("*.json"))) == 2
    assert json.loads((bundle / "outputs" / "final.json").read_text()) == {"output": "done"}
    assert (bundle / "context").is_dir()


def test_round_trip_directory_and_zip(tmp_path: Path) -> None:
    run_id = _recorded_run()
    store = LocalStore()
    original_events = store.read_events(run_id)
    bundle = export_run(run_id, tmp_path)
    with pytest.raises(BundleError, match="already exists"):
        import_run(bundle)
    assert import_run(bundle, overwrite=True) == run_id
    assert store.read_events(run_id) == original_events

    other = LocalStore(tmp_path / "other-home")
    zipped = export_run(run_id, tmp_path / "z", archive=True)
    assert zipped.suffix == ".zip"
    assert import_run(zipped, store=other) == run_id
    assert other.read_manifest(run_id).output == "done"
    assert [e.seq for e in other.read_events(run_id)] == [e.seq for e in original_events]


def test_import_rejects_bad_paths(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="does not exist"):
        import_run(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(BundleError, match="manifest\\.json missing"):
        import_run(tmp_path / "empty")
