"""Datasets: turning production runs into regression material."""

from __future__ import annotations

import pytest

from rewyn.agents.agent import Agent
from rewyn.evaluation.dataset import Dataset, DatasetError, list_datasets
from rewyn.runtime.recorder import default_recorder
from rewyn.testing.fake_model import FakeModel


def record(answer: str = "Paris", question: str = "capital of France?") -> str:
    agent = Agent(FakeModel([answer]), name="qa")
    run_id = agent.run(question).run_id
    default_recorder().flush()
    return run_id


def test_a_production_run_becomes_a_dataset_item():
    run_id = record()
    dataset = Dataset(name="qa-critical")
    item = dataset.add_run(run_id)
    assert item.input == "capital of France?"
    assert item.expected == "Paris"
    assert item.source_run_id == run_id
    assert item.metadata["captured_tools"] == []


def test_expected_output_can_be_overridden_when_capturing():
    item = Dataset(name="qa").add_run(record(), expected="Paris, France")
    assert item.expected == "Paris, France"


def test_datasets_round_trip_through_local_storage():
    dataset = Dataset(name="qa-critical", description="the ones that matter")
    dataset.add("capital of France?", "Paris", tags=["geography"])
    path = dataset.save()
    assert path.exists()

    loaded = Dataset.load("qa-critical")
    assert loaded.name == dataset.name
    assert loaded.description == dataset.description
    assert len(loaded) == 1
    assert loaded.items[0].expected == "Paris"
    assert loaded.fingerprint() == dataset.fingerprint()


def test_loading_a_missing_dataset_is_an_error():
    with pytest.raises(DatasetError, match="not found"):
        Dataset.load("nope")


def test_load_or_create_returns_an_empty_dataset():
    assert len(Dataset.load_or_create("fresh")) == 0


def test_fingerprint_changes_when_items_change():
    dataset = Dataset(name="qa")
    dataset.add("a", "b")
    before = dataset.fingerprint()
    dataset.add("c", "d")
    assert dataset.fingerprint() != before


def test_versions_can_be_bumped():
    dataset = Dataset(name="qa")
    assert dataset.bump().version == "2"
    dataset.version = "alpha"
    with pytest.raises(DatasetError):
        dataset.bump()


def test_from_runs_captures_several_runs():
    ids = [record("Paris"), record("Berlin", "capital of Germany?")]
    dataset = Dataset.from_runs("captured", ids, tags=["prod"])
    assert len(dataset) == 2
    assert {i.expected for i in dataset} == {"Paris", "Berlin"}
    assert all("prod" in i.tags for i in dataset)


def test_filtering_by_tag():
    dataset = Dataset(name="qa")
    dataset.add("a", "b", tags=["critical"])
    dataset.add("c", "d", tags=["nice-to-have"])
    assert len(dataset.filter(tags=["critical"])) == 1
    assert len(dataset.filter()) == 2


def test_jsonl_round_trip(tmp_path):
    dataset = Dataset(name="qa")
    dataset.add("a", "b", tags=["x"])
    path = dataset.to_jsonl(tmp_path / "cases.jsonl")
    restored = Dataset.from_jsonl("qa", path)
    assert len(restored) == 1
    assert restored.items[0].input == "a"
    assert restored.items[0].tags == ["x"]


def test_listing_datasets():
    Dataset(name="one").add("a", "b")
    Dataset(name="one").save()
    Dataset(name="two").save()
    names = {d.name for d in list_datasets()}
    assert {"one", "two"} <= names


def test_dataset_is_a_versioned_dependency():
    dataset = Dataset(name="qa", version="3")
    dataset.add("a", "b")
    dependency = dataset.dependency
    assert dependency.kind == "dataset"
    assert dependency.version == "3"
    assert dependency.metadata["items"] == 1


def test_items_are_reachable_by_index_and_by_id():
    dataset = Dataset(name="qa")
    item = dataset.add("a", "b")
    assert dataset[0] is item
    assert dataset.get(item.id) is item
    assert dataset.get("item_missing") is None


def test_items_can_be_extended_in_bulk():
    from rewyn.evaluation.dataset import DatasetItem

    dataset = Dataset(name="qa")
    dataset.extend([DatasetItem(input="a"), DatasetItem(input="b")])
    assert len(dataset) == 2


def test_a_non_text_input_renders_as_json():
    item = Dataset(name="qa").add({"question": "capital?"}, "Paris")
    assert item.input_text == '{"question": "capital?"}'


def test_capturing_accepts_a_loaded_recording_and_a_live_run():
    from rewyn.core.run import start_run
    from rewyn.replay.recorder import RecordedRun

    recorded = RecordedRun.load(record())
    dataset = Dataset(name="qa")
    assert dataset.add_run(recorded).source_run_id == recorded.id

    with start_run("live", record=False) as run:
        run.manifest.output = "Berlin"
    assert dataset.add_run(run).expected == "Berlin"


def test_a_corrupt_dataset_file_is_skipped_by_the_listing(rewyn_home):
    Dataset(name="good").add("a", "b")
    Dataset(name="good").save()
    broken = Dataset.directory() / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert "broken" not in {d.name for d in list_datasets()}


def test_capturing_an_unsupported_object_is_an_error():
    with pytest.raises(DatasetError):
        Dataset(name="qa").add_run(object())
