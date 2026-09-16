"""Replay, compare and save-as-test (UI spec §20-§25).

The workspaces are where a developer changes AI behaviour deliberately, so
these tests check the two properties that make that trustworthy: a replay
says what it will do before it does it, and a comparison never states a
hypothesis as a fact.
"""

from __future__ import annotations

import httpx
import pytest

from rewyn.replay.recorder import RecordedRun
from rewyn.ui import schemas as s
from rewyn.ui import workspaces
from rewyn.ui.server import PREFIX


def request(**components: tuple[str, str | None]) -> s.ReplayRequest:
    return s.ReplayRequest(
        components=[
            s.ReplayComponent(component=name, mode=mode, value=value)  # type: ignore[arg-type]
            for name, (mode, value) in components.items()
        ]
    )


def test_the_plan_offers_every_control_the_spec_lists(recorded: RecordedRun):
    """UI §21: nine components, four modes each."""
    plan = workspaces.plan_replay(recorded, s.ReplayRequest())
    assert [c.component for c in plan.components] == list(s.REPLAY_COMPONENTS)
    assert plan.mode == "reconstruct"
    assert all(c.mode == "original" for c in plan.components)
    assert all(c.effect for c in plan.components), "every control explains itself"


def test_changing_the_model_makes_it_a_prompt_replay(recorded: RecordedRun):
    plan = workspaces.plan_replay(recorded, request(model=("new", "fake:other")))
    assert plan.mode == "prompt"
    model = next(c for c in plan.components if c.component == "model")
    assert model.supported
    assert "fake:other" in model.effect
    assert workspaces.replay_arguments(plan)["model"] == "fake:other"


def test_temperature_and_instructions_reach_the_engine(recorded: RecordedRun):
    """UI §21 lists both; they are only meaningful with a replacement model."""
    plan = workspaces.plan_replay(
        recorded,
        request(
            model=("new", "fake:other"), temperature=("new", "0.9"), system=("new", "Be terse.")
        ),
    )
    arguments = workspaces.replay_arguments(plan)
    assert arguments["temperature"] == 0.9
    assert arguments["system"] == "Be terse."
    assert all(c.supported for c in plan.components if c.mode == "new")

    alone = workspaces.plan_replay(recorded, request(temperature=("new", "0.9")))
    temperature = next(c for c in alone.components if c.component == "temperature")
    assert not temperature.supported
    assert "replacement model" in temperature.effect


def test_controls_that_only_come_from_the_recording_say_so(recorded: RecordedRun):
    """UI §21 and §48: the console explains, it does not pretend."""
    plan = workspaces.plan_replay(recorded, request(memory=("live", None)))
    memory = next(c for c in plan.components if c.component == "memory")
    assert not memory.supported
    assert "recording" in memory.effect

    default = workspaces.plan_replay(recorded, s.ReplayRequest())
    for name in ("prompt", "memory", "skills", "mcp"):
        component = next(c for c in default.components if c.component == name)
        assert component.supported
        assert "record" in component.effect


async def test_a_reconstruct_replay_reproduces_the_run(client: httpx.AsyncClient, run_id: str):
    """UI §20: the default replay costs nothing and calls no provider."""
    started = await client.post(f"{PREFIX}/runs/{run_id}/replay", json={"components": []})
    assert started.status_code == 202
    job = started.json()
    assert job["plan"]["mode"] == "reconstruct"

    finished = await _await_replay(client, job["id"])
    assert finished["status"] == "succeeded"
    assert finished["identical"] is True
    assert finished["faithful"] is True
    # Nothing new was spent: a reconstruction re-emits the recorded costs.
    assert finished["cost"] == finished["original_cost"]
    assert finished["output"] == finished["original_output"]


async def test_a_replay_the_console_cannot_run_is_refused_with_a_way_forward(
    client: httpx.AsyncClient, run_id: str
):
    """UI §48: the failure names the recovery action."""
    response = await client.post(
        f"{PREFIX}/runs/{run_id}/replay",
        json={"components": [{"component": "tools", "mode": "live", "value": None}]},
    )
    assert response.status_code == 422
    problem = response.json()["detail"]
    assert problem["error"] == "This replay cannot run here"
    assert problem["action"] == "Replay with recorded responses"


async def test_a_missing_replay_explains_itself(client: httpx.AsyncClient):
    response = await client.get(f"{PREFIX}/replays/replay_nope")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "Replay not found"


async def _await_replay(client: httpx.AsyncClient, job_id: str) -> dict:
    import asyncio

    for _ in range(50):
        body = (await client.get(f"{PREFIX}/replays/{job_id}")).json()
        if body["status"] in ("succeeded", "failed"):
            return body
        await asyncio.sleep(0.02)
    pytest.fail("the replay never finished")


async def test_comparing_two_runs_separates_observation_from_inference(
    client: httpx.AsyncClient, run_id: str
):
    """UI §22 and §23."""
    from tests.ui.conftest import record_demo_run

    other = await record_demo_run(user="sam")
    response = await client.get(f"{PREFIX}/runs/{other}/diff", params={"against": run_id})
    assert response.status_code == 200
    diff = response.json()

    assert diff["run_a"] == run_id
    assert diff["run_b"] == other
    assert [d["dimension"] for d in diff["dimensions"][:6]] == [
        "model",
        "prompt",
        "context",
        "memory",
        "tools",
        "mcp",
    ]
    assert "output" not in [d["dimension"] for d in diff["dimensions"]], (
        "output, cost and latency get their own rows with a delta (UI §22)"
    )
    for difference in diff["differences"]:
        assert difference["description"], "an observed change describes itself"
    for explanation in diff["explanations"]:
        assert 0.0 <= explanation["confidence"] <= 1.0, "a hypothesis carries its confidence"


async def test_the_console_suggests_what_to_compare_against(client: httpx.AsyncClient, run_id: str):
    """UI §58 step 5: "compare with previous successful run"."""
    from tests.ui.conftest import record_demo_run

    later = await record_demo_run()
    candidates = (await client.get(f"{PREFIX}/runs/{later}/comparable")).json()
    assert [c["id"] for c in candidates] == [run_id]
    assert candidates[0]["status"] == "succeeded"


async def test_a_run_becomes_a_regression_case(client: httpx.AsyncClient, run_id: str):
    """UI §24: the production failure becomes a permanent test."""
    response = await client.post(
        f"{PREFIX}/runs/{run_id}/save-as-test",
        json={
            "dataset": "credit-regression",
            "expected": "Human-approved limit",
            "evaluator": "task-success",
            "severity": "critical",
            "tags": ["from-console"],
        },
    )
    assert response.status_code == 201
    dataset = response.json()
    assert dataset["name"] == "credit-regression"
    assert dataset["cases"] == 1
    case = dataset["items"][0]
    assert case["source_run_id"] == run_id
    assert case["expected"] == "Human-approved limit"
    assert case["evaluator"] == "task-success"
    assert case["severity"] == "critical"
    assert case["tags"] == ["from-console"]

    listed = (await client.get(f"{PREFIX}/datasets")).json()
    assert [d["name"] for d in listed] == ["credit-regression"]
    detail = (await client.get(f"{PREFIX}/datasets/credit-regression")).json()
    assert detail["items"][0]["id"] == case["id"]


async def test_the_expectation_defaults_to_what_the_run_actually_did(
    client: httpx.AsyncClient, run_id: str
):
    """A golden example: the run's own output becomes the expectation."""
    response = await client.post(f"{PREFIX}/runs/{run_id}/save-as-test", json={"dataset": "golden"})
    case = response.json()["items"][0]
    assert case["expected"]
    assert "250,000" in str(case["expected"])


async def test_a_missing_dataset_points_at_making_one(client: httpx.AsyncClient):
    """UI §47: the empty state teaches."""
    response = await client.get(f"{PREFIX}/datasets/nope")
    assert response.status_code == 404
    assert response.json()["detail"]["action"] == "Create one from a run"


async def test_a_replay_is_not_offered_as_something_to_compare_against(
    client: httpx.AsyncClient, run_id: str
):
    """A replay of a run is not its predecessor (UI §58 step 5).

    Replays are recorded like any other run -- they are inspectable, and they
    appear in the runs table -- but the compare picker is asking which earlier
    execution to hold this one against, and its own replay is not an answer.
    """
    started = await client.post(f"{PREFIX}/runs/{run_id}/replay", json={"components": []})
    replay = await _await_replay(client, started.json()["id"])
    assert replay["status"] == "succeeded"

    listed = (await client.get(f"{PREFIX}/runs")).json()
    assert replay["replay_run_id"] in [r["id"] for r in listed["runs"]]

    candidates = (await client.get(f"{PREFIX}/runs/{run_id}/comparable")).json()
    assert replay["replay_run_id"] not in [c["id"] for c in candidates]
