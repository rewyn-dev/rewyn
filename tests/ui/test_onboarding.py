"""The first run: what this is, and what to do (UI spec §47, §59).

The console's other tests all start from a recorded run. These start from
nothing, which is where a new reader starts.
"""

from __future__ import annotations

import httpx
import pytest

from rewyn.storage.local import LocalStore
from rewyn.ui import demo, onboarding
from rewyn.ui import schemas as s
from rewyn.ui.server import PREFIX


def test_an_empty_project_still_says_what_the_tool_is_for():
    """UI §47: an empty state teaches the product."""
    document = onboarding.document(
        place=onboarding.location(surface="local", project="default", home="/tmp/.rewyn"),
        counts=s.ConsoleCounts(),
    )
    assert document.empty
    assert document.summary
    assert [step.key for step in document.steps] == [
        "install",
        "record",
        "inspect",
        "test",
        "prove",
    ]
    assert [job.key for job in document.jobs] == ["debug", "prove", "explain", "cost"]


def test_nothing_is_claimed_to_be_done_that_is_not():
    counts = s.ConsoleCounts()
    steps = {step.key: step for step in onboarding.steps(counts)}
    assert steps["install"].done, "the console is running, so the SDK is installed"
    assert not steps["record"].done
    assert not steps["prove"].done

    done = onboarding.steps(s.ConsoleCounts(runs=4, datasets=1, reports=1))
    assert all(step.done for step in done)


def test_every_unready_job_says_what_would_ready_it():
    """UI §47: a screen that cannot answer yet says what unlocks it."""
    for job in onboarding.jobs(s.ConsoleCounts()):
        assert not job.ready
        assert job.needs, f"{job.key} is not ready and does not say why"
    for job in onboarding.jobs(s.ConsoleCounts(runs=9, datasets=1, reports=1, versions=2)):
        assert job.ready
        assert not job.needs


def test_drift_needs_two_versions_not_two_runs():
    """One run at one version is not a trend, and the copy must not imply it is."""
    explain = next(j for j in onboarding.jobs(s.ConsoleCounts(runs=50)) if j.key == "explain")
    assert not explain.ready
    assert "two versions" in explain.needs


@pytest.mark.parametrize("surface", ["local", "cloud"])
def test_the_console_says_what_a_project_is_on_this_surface(surface: str):
    """UI §41: a project is not something the console creates."""
    place = onboarding.location(
        surface=surface,  # type: ignore[arg-type]
        project="acme",
        home="/tmp/.rewyn" if surface == "local" else None,
    )
    assert place.how_to_change
    if surface == "local":
        assert "REWYN_HOME" in place.how_to_change
        assert "nothing to create" in place.how_to_change
    else:
        assert "create-project" in place.how_to_change


async def test_a_cold_console_answers_the_question_a_new_reader_has(
    client: httpx.AsyncClient, store: LocalStore
):
    document = (await client.get(f"{PREFIX}/onboarding")).json()
    assert document["empty"] is True
    assert document["can_load_demo"] is True
    assert document["counts"]["runs"] == 0
    assert str(store.home.resolve()) == document["location"]["home"]
    assert any(step["code"] for step in document["steps"]), "it hands you something to run"


async def test_the_demo_loads_fills_the_console_and_comes_out_clean(
    client: httpx.AsyncClient, store: LocalStore
):
    """The bootstrapping problem: a demo makes the tool legible before it is useful."""
    loaded = (await client.post(f"{PREFIX}/demo")).json()
    assert loaded["demo_loaded"] is True
    assert loaded["empty"] is False
    counts = loaded["counts"]
    assert counts["runs"] >= 10
    assert counts["agents"] == 2
    assert counts["versions"] == 2, "two versions, so drift and releases have two points"
    assert counts["failures"] >= 3, "a failure cluster, so incidents has an incident"
    assert counts["datasets"] == 1

    # The screens it exists to light up actually light up.
    assert (await client.get(f"{PREFIX}/incidents")).json()
    assert (await client.get(f"{PREFIX}/agents")).json()
    assert (await client.get(f"{PREFIX}/evaluations")).json()["evaluators"]

    cleared = (await client.delete(f"{PREFIX}/demo")).json()
    assert cleared["demo_loaded"] is False
    assert cleared["counts"]["runs"] == 0
    assert (await client.get(f"{PREFIX}/datasets")).json() == []


async def test_clearing_the_demo_leaves_your_own_runs_alone(
    client: httpx.AsyncClient, store: LocalStore, run_id: str
):
    """Exploring the product must not be able to delete your data."""
    await client.post(f"{PREFIX}/demo")
    await client.delete(f"{PREFIX}/demo")

    assert store.exists(run_id), "a run that was not tagged demo is untouched"
    remaining = (await client.get(f"{PREFIX}/runs")).json()
    assert run_id in [run["id"] for run in remaining["runs"]]


async def test_capabilities_carry_the_counts_the_navigation_needs(
    client: httpx.AsyncClient, run_id: str
):
    """UI §4, §47: an entry is marked rather than hidden, which needs numbers."""
    capabilities = (await client.get(f"{PREFIX}/capabilities")).json()
    assert capabilities["counts"]["runs"] == 1
    assert capabilities["location"]["surface"] == "local"
    assert capabilities["demo_loaded"] is False


def test_the_demo_is_removable_because_every_run_of_it_is_tagged(store: LocalStore):
    demo.load(store)
    tagged = [m for m in store.list_runs(limit=100) if demo.DEMO_TAG in m.tags]
    assert len(tagged) == len(store.list_runs(limit=100))

    assert demo.is_loaded(store)
    assert demo.clear(store) == len(tagged)
    assert not demo.is_loaded(store)
