"""The development workspace and the loop it closes (UI spec §37, §59, §60).

These drive the real endpoints against a recorded demo run, so a stage that
claims to be ready is one the console could actually open.
"""

from __future__ import annotations

import httpx
import pytest

from rewyn.ui import lifecycle
from rewyn.ui import schemas as s
from rewyn.ui.server import PREFIX


def agent_detail(**kwargs: object) -> s.AgentDetail:
    base: dict[str, object] = {
        "name": "refund-agent",
        "version": "3",
        "versions": 3,
        "runs": 12,
        "success_rate": 100.0,
        "dependencies": [
            {"kind": "model", "name": "gpt-5", "version": "1"},
            {"kind": "skill", "name": "refund-policy", "version": "19"},
        ],
    }
    base.update(kwargs)
    return s.AgentDetail.model_validate(base)


def test_the_workspace_stacks_the_bands_the_spec_draws():
    """UI §37: config → run → inspect → replay → evaluate → regression."""
    view = lifecycle.workspace(agent_detail())
    assert [stage.key for stage in view.stages] == [key for key, _, _ in lifecycle.STAGES]
    assert view.components[0].href == "/models/gpt-5"
    assert view.components[1].href == "/skills/refund-policy"


def test_every_band_carries_its_north_star_question():
    """UI §59: every important page answers one of the nine questions."""
    view = lifecycle.workspace(agent_detail())
    assert all(stage.question.endswith("?") for stage in view.stages)
    assert all(stage.question.endswith("?") for stage in view.loop)


def test_the_loop_is_the_nine_steps_of_the_long_term_vision():
    """UI §60."""
    view = lifecycle.workspace(agent_detail())
    assert [stage.key for stage in view.loop] == [key for key, _, _ in lifecycle.LOOP]
    assert all(stage.href for stage in view.loop), "a loop step with no target is not navigable"


def test_a_stage_with_no_evidence_says_what_would_start_it():
    """UI §47: an empty state teaches."""
    view = lifecycle.workspace(agent_detail(runs=0, recent_runs=[]))
    regression = next(stage for stage in view.stages if stage.key == "regression")
    assert not regression.ready
    assert "Save a run as a test" in regression.detail
    assert view.next_step.startswith("Run:")


def test_the_next_step_names_the_broken_stage_before_the_missing_one():
    report = s.RegressionSummary(
        id="rep_1",
        dataset="refunds",
        created_at="2026-01-01T00:00:00Z",
        tests=10,
        succeeded=7,
        passed=False,
    )
    view = lifecycle.workspace(agent_detail(), reports=[report])
    assert view.next_step.startswith("Regression:")
    assert "7/10" in view.next_step


def test_a_blocked_release_makes_the_release_step_fail():
    release = s.ReleaseView(
        application="refund-agent",
        version="3",
        status="blocked",
        blocked_by=["success rate 70% is below the 90% gate"],
    )
    loop = lifecycle.loop(agent_detail(), release=release)
    step = next(stage for stage in loop if stage.key == "release")
    assert step.status == "fail"
    assert "90% gate" in step.detail


async def test_the_workspace_endpoint_answers_for_a_recorded_agent(
    client: httpx.AsyncClient, run_id: str
):
    response = await client.get(f"{PREFIX}/workspace", params={"agent": "acme-credit"})
    assert response.status_code == 200
    view = s.WorkspaceView.model_validate(response.json())

    assert view.agent == "acme-credit"
    assert view.latest_run is not None
    assert view.latest_run.id == run_id
    assert view.components, "a recorded run knows what the agent was made of"

    ready = {stage.key for stage in view.stages if stage.ready}
    assert {"config", "run", "replay"} <= ready
    inspect = {stage.key: stage for stage in view.stages}
    assert inspect["context"].count, "the demo assembles context"
    assert inspect["tools"].count, "the demo calls tools"
    assert view.next_step


async def test_the_workspace_of_an_agent_that_never_ran_explains_itself(
    client: httpx.AsyncClient,
):
    response = await client.get(f"{PREFIX}/workspace", params={"agent": "nope"})
    assert response.status_code == 404
    problem = response.json()["detail"]
    assert problem["action"] == "Browse runs"


@pytest.mark.parametrize("stage", [key for key, _, _ in lifecycle.LOOP])
def test_no_loop_step_is_left_without_a_state(stage: str):
    view = lifecycle.workspace(agent_detail())
    step = next(s_ for s_ in view.loop if s_.key == stage)
    assert step.summary or step.detail
