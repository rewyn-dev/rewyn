"""BUILD pages, agent pages, evaluations and regression (UI spec §26-§30).

"What is my AI made of?" is answered from what runs recorded, so these tests
record real runs and then ask the console what it can see.
"""

from __future__ import annotations

import httpx
import pytest

from rewyn.agents.agent import Agent
from rewyn.core.run import start_run
from rewyn.evaluation.dataset import Dataset
from rewyn.evaluation.evaluator import Subject, evaluator
from rewyn.evaluation.metrics import not_empty
from rewyn.evaluation.regression import arun_regression
from rewyn.runtime.recorder import default_recorder
from rewyn.storage.local import LocalStore
from rewyn.testing.fake_model import FakeModel
from rewyn.tools.tool import tool
from rewyn.ui.server import PREFIX


@tool
def lookup(topic: str) -> str:
    """Look a topic up."""
    return f"about {topic}"


async def record_agent(version: str, *, answer: str = "done", fail: bool = False) -> str:
    """One run of a versioned agent, so the registries have something to show."""
    agent = Agent(
        model=FakeModel([FakeModel.tool_call("lookup", {"topic": "x"}), answer]),
        name="refund-agent",
        version=version,
        tools=[lookup],
    )
    async with start_run("refund-agent", metadata={"environment": "production"}) as run:
        result = await agent.arun("refund please")
        run.manifest.output = result.output
        if fail:
            run.manifest.status = run.manifest.status.FAILED
            run.manifest.error = "ToolError: downstream refused"
    default_recorder().flush()
    return run.id


@pytest.fixture
async def versioned(store: LocalStore) -> list[str]:
    return [await record_agent("17"), await record_agent("18")]


async def test_the_agents_page_lists_what_has_run(client: httpx.AsyncClient, versioned: list[str]):
    """UI §29."""
    agents = (await client.get(f"{PREFIX}/agents")).json()
    refund = next(a for a in agents if a["name"] == "refund-agent")
    assert refund["versions"] == 2
    assert refund["version"] == "18", "the newest version is the headline"
    assert refund["runs"] == 2
    assert refund["success_rate"] == 100.0
    assert refund["environments"] == ["production"]
    assert refund["model"] == "fake:fake-1"
    assert refund["tools"] == 1


async def test_an_agent_page_carries_its_tabs(client: httpx.AsyncClient, versioned: list[str]):
    """UI §29: overview, runs, configuration, dependencies, versions."""
    detail = (await client.get(f"{PREFIX}/agents/refund-agent")).json()
    assert detail["name"] == "refund-agent"
    assert [v["version"] for v in detail["version_history"]] == ["18", "17"]
    assert detail["version_history"][0]["runs"] == 1
    kinds = {d["kind"] for d in detail["dependencies"]}
    assert {"agent", "model", "tool"} <= kinds
    assert [r["id"] for r in detail["recent_runs"]] == list(reversed(versioned))


async def test_an_unknown_agent_says_agents_appear_once_they_run(client: httpx.AsyncClient):
    response = await client.get(f"{PREFIX}/agents/ghost")
    assert response.status_code == 404
    assert "once they have run" in response.json()["detail"]["detail"]


async def test_comparing_two_agent_versions_shows_exactly_what_changed(
    client: httpx.AsyncClient, versioned: list[str]
):
    """UI §30: v17 → v18."""
    diff = (await client.get(f"{PREFIX}/agents/refund-agent/versions/17..18")).json()
    assert diff["before_version"] == "17"
    assert diff["after_version"] == "18"
    changed = [f for f in diff["findings"] if f["name"] == "refund-agent"]
    assert changed, "the agent's own version change is a finding"
    assert changed[0]["before_version"] == "17"
    assert changed[0]["after_version"] == "18"
    assert changed[0]["kind"] == "version_changed"
    assert diff["unchanged"] > 0, "everything else stayed put"


async def test_comparing_a_version_that_never_ran_says_so(
    client: httpx.AsyncClient, versioned: list[str]
):
    response = await client.get(f"{PREFIX}/agents/refund-agent/versions/17..99")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "Version not found"


@pytest.mark.parametrize(
    ("page", "expected"),
    [("models", "fake:fake-1"), ("tools", "lookup"), ("agents", "refund-agent")],
)
async def test_the_build_pages_roll_dependencies_up_by_name(
    client: httpx.AsyncClient, versioned: list[str], page: str, expected: str
):
    """UI §4's BUILD group, from the dependencies runs already record."""
    entries = (await client.get(f"{PREFIX}/registry/{page}")).json()
    assert expected in [e["name"] for e in entries]
    entry = next(e for e in entries if e["name"] == expected)
    assert entry["runs"] >= 1
    assert entry["last_used_at"]


async def test_a_build_entry_shows_its_versions_and_who_used_it(
    client: httpx.AsyncClient, versioned: list[str]
):
    detail = (await client.get(f"{PREFIX}/registry/tools/lookup")).json()
    assert detail["name"] == "lookup"
    assert detail["used_by"] == ["refund-agent"]
    assert detail["runs"] == 2
    assert len(detail["recent_runs"]) == 2


async def test_an_unknown_registry_page_lists_the_real_ones(client: httpx.AsyncClient):
    response = await client.get(f"{PREFIX}/registry/nonsense")
    assert response.status_code == 404
    assert "models" in response.json()["detail"]["detail"]


async def test_evaluations_report_score_distributions(
    client: httpx.AsyncClient, versioned: list[str]
):
    """UI §27."""

    @evaluator
    def groundedness(subject: Subject) -> float:
        return 0.8 if subject.run_id else 0.2

    for run_id in versioned:
        with start_run("evaluation"):
            await groundedness.ascore(Subject(output="done", run_id=run_id))
    default_recorder().flush()

    view = (await client.get(f"{PREFIX}/evaluations")).json()
    assert view["scores"] == 2
    assert view["runs_scored"] == 2
    summary = next(e for e in view["evaluators"] if e["name"] == "groundedness")
    assert summary["mean"] == pytest.approx(0.8)
    assert summary["pass_rate"] == 100.0
    assert len(summary["distribution"]) == 10
    assert sum(bucket["count"] for bucket in summary["distribution"]) == 2
    assert summary["distribution"][8]["count"] == 2, "0.8 lands in the 0.8-0.9 bucket"


async def test_regression_reports_are_listed_and_readable(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §26: the baseline/candidate table and the cases that got worse."""
    dataset = Dataset(name="refund-regression")
    dataset.add("refund please", "done")
    dataset.add("other request", "done")

    def good(payload: str) -> str:
        return "done"

    def regressed(payload: str) -> str:
        # The second case stops producing an answer: one case becomes worse.
        return "done" if "refund" in payload else ""

    baseline = await arun_regression(dataset, good, evaluators=[not_empty()])
    baseline.target = "refund-agent"
    baseline.save(home=store.home)

    candidate = await arun_regression(dataset, regressed, evaluators=[not_empty()])
    candidate.target = "refund-agent"
    candidate.compare_to(baseline)
    candidate.save(home=store.home)

    listed = (await client.get(f"{PREFIX}/regression")).json()
    assert {r["id"] for r in listed} == {baseline.id, candidate.id}

    detail = (await client.get(f"{PREFIX}/regression/{candidate.id}")).json()
    assert detail["tests"] == 2
    assert detail["success_rate"] == 50.0
    assert detail["success_delta"] == -50.0
    assert detail["baseline"]["id"] == baseline.id
    assert detail["baseline"]["success_rate"] == 100.0
    assert [c["item_id"] for c in detail["regressions"]] == [dataset.items[1].id]
    assert detail["regressions"][0]["regressed"] is True
    assert detail["metrics"][0]["name"] == "not_empty"


async def test_a_missing_report_points_back_at_the_list(client: httpx.AsyncClient):
    response = await client.get(f"{PREFIX}/regression/report_nope")
    assert response.status_code == 404
    assert response.json()["detail"]["action"] == "Browse regression runs"


async def test_an_experiment_produces_the_command_that_runs_it(client: httpx.AsyncClient):
    """UI §26 and §48: the console says how, because it cannot run your code."""
    plan = (
        await client.post(
            f"{PREFIX}/experiments:plan",
            json={
                "dataset": "refund-regression",
                "target": "app.agents:refund",
                "baseline": "latest",
                "evaluators": ["app.evals:task_success"],
                "min_success": 0.95,
                "concurrency": 4,
            },
        )
    ).json()
    assert plan["command"] == (
        "rewyn test refund-regression --target app.agents:refund "
        "--evaluator app.evals:task_success --baseline latest "
        "--min-success 0.95 --concurrency 4"
    )
    assert "runs where your code is" in plan["explanation"]
