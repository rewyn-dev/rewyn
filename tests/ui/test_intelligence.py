"""Dependencies, drift, cost, releases and experiments (UI §28, §31-§33, §40, §43).

These screens make claims about causes and about whether something is safe to
deploy, so the tests are mostly about what the console refuses to claim: no
hierarchy that was not observed, no drift cause that did not change, and no
release called ready without a report that says so.
"""

from __future__ import annotations

import httpx
import pytest

from rewyn.agents.agent import Agent
from rewyn.core.run import start_run
from rewyn.evaluation.dataset import Dataset
from rewyn.evaluation.metrics import not_empty
from rewyn.evaluation.regression import Thresholds, arun_regression
from rewyn.runtime.recorder import default_recorder
from rewyn.storage.local import LocalStore
from rewyn.testing.fake_model import FakeModel
from rewyn.tools.tool import tool
from rewyn.ui.server import PREFIX


@tool
def lookup(topic: str) -> str:
    """Look a topic up."""
    return f"about {topic}"


class DownstreamError(Exception):
    """Raised to make a run genuinely fail, rather than relabelling one."""


async def record(version: str = "1", *, answer: str = "done", failed: bool = False) -> str:
    agent = Agent(
        model=FakeModel([FakeModel.tool_call("lookup", {"topic": "x"}), answer]),
        name="refund-agent",
        version=version,
        tools=[lookup],
    )
    try:
        async with start_run("refund-agent", metadata={"environment": "production"}) as run:
            result = await agent.arun("refund please")
            run.manifest.output = result.output
            if failed:
                raise DownstreamError("downstream refused")
    except DownstreamError:
        pass
    default_recorder().flush()
    return run.id


async def test_the_dependency_map_draws_only_what_was_observed(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §31: every node is clickable, and no hierarchy is invented."""
    await record()
    body = (await client.get(f"{PREFIX}/dependencies", params={"agent": "refund-agent"})).json()

    assert body["root"] == "agent:refund-agent"
    kinds = {node["kind"] for node in body["nodes"]}
    assert {"agent", "model", "tool"} <= kinds

    tool_node = next(node for node in body["nodes"] if node["name"] == "lookup")
    assert tool_node["href"] == "/tools/lookup", "a node has somewhere to click through to"

    # Everything hangs off the root, because nothing claimed ownership of it.
    assert all(edge["source"] == body["root"] for edge in body["edges"])
    assert {edge["target"] for edge in body["edges"]} == {
        node["id"] for node in body["nodes"] if node["id"] != body["root"]
    }


async def test_an_mcp_server_owns_the_tools_it_exposed(client: httpx.AsyncClient):
    """UI §31: the second level is drawn where a relationship was recorded."""
    from tests.ui.conftest import record_demo_run

    await record_demo_run()
    body = (await client.get(f"{PREFIX}/dependencies", params={"agent": "acme-credit"})).json()

    exposes = [edge for edge in body["edges"] if edge["relation"] == "exposes"]
    assert exposes, "the MCP server's tools hang off the server, not the agent"
    from_server = [edge for edge in exposes if edge["source"] == "mcp_server:salesforce"]
    assert "tool:salesforce_lookup_account" in {edge["target"] for edge in from_server}
    # The agent no longer claims a tool that something else exposed.
    assert "tool:salesforce_lookup_account" not in {
        edge["target"] for edge in body["edges"] if edge["source"] == body["root"]
    }


async def test_mapping_an_agent_that_never_ran_says_so(client: httpx.AsyncClient):
    response = await client.get(f"{PREFIX}/dependencies", params={"agent": "ghost"})
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "Nothing to map"


async def test_drift_reports_a_version_change_as_the_cause(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §32: evidence-based."""
    await record(version="17")
    await record(version="17")
    await record(version="18", failed=True)
    await record(version="18", failed=True)

    body = (await client.get(f"{PREFIX}/drift", params={"agent": "refund-agent"})).json()

    assert body["expected_success"] == 100.0
    assert body["current_success"] == 0.0
    assert body["drifted"] is True
    assert body["silent"] is False
    causes = {(cause["dependency_kind"], cause["name"]) for cause in body["causes"]}
    assert ("agent", "refund-agent") in causes
    assert body["unchanged"], "what did not change is evidence too"


async def test_drift_with_no_cause_says_it_cannot_explain_it(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §32 and §23: never claim certainty without evidence."""
    await record(version="1")
    await record(version="1")
    await record(version="1", failed=True)
    await record(version="1", failed=True)

    body = (await client.get(f"{PREFIX}/drift", params={"agent": "refund-agent"})).json()
    assert body["silent"] is True
    assert [cause["kind"] for cause in body["causes"]] == ["unexplained"]
    assert "outside the manifest" in body["causes"][0]["detail"]


async def test_drift_needs_history_before_it_will_guess(client: httpx.AsyncClient):
    await record()
    response = await client.get(f"{PREFIX}/drift", params={"agent": "refund-agent"})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "Not enough history"


async def test_cost_answers_in_cost_per_successful_task(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §33: more useful than raw token charts."""
    await record()
    await record(failed=True)

    body = (await client.get(f"{PREFIX}/cost", params={"group_by": "agent"})).json()
    assert body["group_by"] == "agent"
    assert body["runs"] == 2
    assert body["succeeded"] == 1
    assert body["slices"][0]["key"] == "refund-agent"
    assert body["slices"][0]["runs"] == 2
    # Two runs of equal cost, one of which failed: a failure is not a saving.
    assert body["per_successful_task"] == pytest.approx(body["total"])
    assert body["categories"]["model"] > 0
    assert body["categories"]["total"] == pytest.approx(body["total"])


@pytest.mark.parametrize("group_by", ["agent", "model", "user", "environment", "time", "tool"])
async def test_cost_groups_by_every_dimension_the_spec_lists(
    client: httpx.AsyncClient, group_by: str
):
    await record()
    response = await client.get(f"{PREFIX}/cost", params={"group_by": group_by})
    assert response.status_code == 200
    assert response.json()["group_by"] == group_by


async def test_an_unsupported_grouping_is_refused_with_a_way_forward(client: httpx.AsyncClient):
    response = await client.get(f"{PREFIX}/cost", params={"group_by": "phase-of-the-moon"})
    assert response.status_code == 400
    assert response.json()["detail"]["action"] == "Group by agent"


async def test_a_version_without_a_report_is_unverified_not_safe(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §43: unverified is a different thing from ready."""
    await record(version="17")
    await record(version="18")

    releases = (await client.get(f"{PREFIX}/releases")).json()
    assert {release["version"] for release in releases} == {"17", "18"}
    assert all(release["status"] == "unverified" for release in releases)
    assert all(release["tests"] == 0 for release in releases)


async def test_a_failed_gate_blocks_the_release_and_says_which(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §43: status BLOCKED, with the gate that blocked it."""
    await record(version="18")

    dataset = Dataset(name="refund-regression")
    dataset.add("refund please", "done")
    dataset.add("second", "done")

    def regressed(payload: str) -> str:
        return "done" if "refund" in payload else ""

    report = await arun_regression(
        dataset,
        regressed,
        evaluators=[not_empty()],
        thresholds=Thresholds(min_success_rate=0.95),
    )
    report.target = "refund-agent"
    report.save(home=store.home)

    releases = (await client.get(f"{PREFIX}/releases", params={"agent": "refund-agent"})).json()
    release = releases[0]
    assert release["status"] == "blocked"
    assert release["tests"] == 2
    assert release["failed"] == 1
    assert release["blocked_by"], "it says which gate failed"
    assert "success" in release["blocked_by"][0].lower()
    assert "pipeline's job" in release["promotion"]

    # UI §43, §54: a blocked version is refused by the server, not by a
    # disabled button. Nothing the browser does can get past this.
    refused = await client.post(
        f"{PREFIX}/releases/{release['id']}/promote", json={"environment": "production"}
    )
    assert refused.status_code == 409
    problem = refused.json()["detail"]
    assert "blocked" in problem["detail"]
    assert problem["href"] == f"/regression/{release['report_id']}"


async def test_an_unverified_version_is_refused_because_unverified_is_not_safe(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §43: no report means nothing showed it is safe."""
    await record(version="18")
    release = (await client.get(f"{PREFIX}/releases")).json()[0]
    assert release["status"] == "unverified"

    refused = await client.post(f"{PREFIX}/releases/{release['id']}/promote", json={})
    assert refused.status_code == 409
    assert "not the same as ready" in refused.json()["detail"]["detail"]


async def test_a_release_can_be_fetched_by_id_and_promoted_once_it_passes(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §43: the promote action, and the decision it records."""
    await record(version="18")

    dataset = Dataset(name="refund-regression")
    dataset.add("refund please", "done")

    def good(payload: str) -> str:
        return "done"

    report = await arun_regression(dataset, good, evaluators=[not_empty()])
    report.target = "refund-agent"
    report.save(home=store.home)

    listed = (await client.get(f"{PREFIX}/releases", params={"agent": "refund-agent"})).json()[0]
    assert listed["id"] == "refund-agent@18"
    assert listed["status"] == "ready"

    one = await client.get(f"{PREFIX}/releases/{listed['id']}")
    assert one.status_code == 200
    assert one.json()["version"] == "18"

    promoted = await client.post(
        f"{PREFIX}/releases/{listed['id']}/promote",
        json={"environment": "production", "note": "gate green"},
    )
    assert promoted.status_code == 200
    record_ = promoted.json()["promoted"]
    assert record_["environment"] == "production"
    assert record_["report_id"] == listed["report_id"], "the decision carries its evidence"
    assert record_["by"]

    # The decision is durable, and shows on the list.
    again = (await client.get(f"{PREFIX}/releases", params={"agent": "refund-agent"})).json()[0]
    assert again["promoted"]["note"] == "gate green"


async def test_a_release_id_that_is_not_one_says_so(client: httpx.AsyncClient):
    """UI §48."""
    assert (await client.get(f"{PREFIX}/releases/nonsense")).status_code == 422
    missing = await client.get(f"{PREFIX}/releases/nope@1")
    assert missing.status_code == 404
    assert missing.json()["detail"]["href"] == "/releases"


async def test_experiments_line_variants_up_and_name_the_winner(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §28: control against variant A against variant B."""
    dataset = Dataset(name="refund-regression")
    dataset.add("refund please", "done")
    dataset.add("second", "done")

    def control(payload: str) -> str:
        return "done" if "refund" in payload else ""

    def better(payload: str) -> str:
        return "done"

    first = await arun_regression(dataset, control, evaluators=[not_empty()])
    first.target = "control"
    first.save(home=store.home)
    second = await arun_regression(dataset, better, evaluators=[not_empty()])
    second.target = "variant-a"
    second.save(home=store.home)

    experiments = (await client.get(f"{PREFIX}/experiments")).json()
    experiment = next(e for e in experiments if e["dataset"] == "refund-regression")
    assert [variant["label"] for variant in experiment["variants"]] == ["Control", "Variant A"]
    assert experiment["variants"][0]["success_rate"] == 50.0
    assert experiment["variants"][1]["success_rate"] == 100.0
    assert experiment["variants"][1]["winner"] is True
    assert "not_empty" in experiment["measured"]


async def test_notifications_are_meaningful_and_deduplicated(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §40: regression, drift and dependency changes -- not a log."""
    await record(version="17")
    await record(version="17")
    await record(version="18", failed=True)
    await record(version="18", failed=True)

    dataset = Dataset(name="refund-regression")
    dataset.add("refund please", "done")

    def broken(payload: str) -> str:
        return ""

    report = await arun_regression(dataset, broken, evaluators=[not_empty()])
    report.target = "refund-agent"
    report.save(home=store.home)

    found = (await client.get(f"{PREFIX}/notifications")).json()
    kinds = {item["kind"] for item in found}
    assert "drift" in kinds
    assert kinds & {"regression", "evaluation"}
    assert len({item["id"] for item in found}) == len(found), "one notification per cause"
    assert all(item["summary"] for item in found)
    assert all(item["href"] for item in found)
