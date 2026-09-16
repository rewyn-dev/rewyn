"""The local console API (UI spec §44, §52).

Every test drives the real application over an in-process ASGI transport: no
server, no socket, no network -- the same discipline the cloud tests use.
"""

from __future__ import annotations

import httpx

from rewyn.storage.local import LocalStore
from rewyn.ui.server import PREFIX, build_app
from tests.ui.conftest import BASE_URL


async def test_health_and_capabilities_describe_the_surface(client: httpx.AsyncClient):
    """UI §45: the UI asks what this surface can do rather than assuming."""
    health = await client.get(f"{PREFIX}/health")
    assert health.status_code == 200
    assert health.json()["surface"] == "local"

    capabilities = (await client.get(f"{PREFIX}/capabilities")).json()
    assert capabilities["surface"] == "local"
    assert capabilities["role"] == "owner"
    assert {
        "runs",
        "timeline",
        "context",
        "graph",
        "replay",
        "compare",
        "datasets",
        "agents",
        "evaluations",
        "regression",
        "live",
        "approvals",
        "dependencies",
        "drift",
        "cost",
        "releases",
        "experiments",
        "notifications",
        "incidents",
        "comments",
        "saved-views",
        "workspace",
        "explain",
    } <= set(capabilities["features"])


async def test_the_runs_endpoint_filters_and_pages(client: httpx.AsyncClient, run_id: str):
    """UI §7."""
    page = (await client.get(f"{PREFIX}/runs")).json()
    assert page["total"] == 1
    assert page["runs"][0]["id"] == run_id
    assert page["runs"][0]["agent"] == "acme-credit"

    filtered = (await client.get(f"{PREFIX}/runs", params={"user": "raj"})).json()
    assert filtered["total"] == 1
    empty = (await client.get(f"{PREFIX}/runs", params={"status": "failed"})).json()
    assert empty["total"] == 0

    facets = (await client.get(f"{PREFIX}/runs/facets")).json()
    assert facets["agent"][0]["value"] == "acme-credit"


async def test_every_run_panel_has_an_endpoint(client: httpx.AsyncClient, run_id: str):
    """UI §8-§19: one fetch per tab, so no tab loads another tab's data."""
    detail = (await client.get(f"{PREFIX}/runs/{run_id}")).json()
    assert detail["run"]["id"] == run_id
    assert detail["panels"]["context"] >= 1

    for panel, key in (
        ("timeline", "entries"),
        ("graph", "nodes"),
        ("context", "assemblies"),
        ("model", "calls"),
        ("prompt", "calls"),
        ("memory", "operations"),
        ("tools", "calls"),
        ("mcp", "servers"),
        ("skills", "skills"),
    ):
        response = await client.get(f"{PREFIX}/runs/{run_id}/{panel}")
        assert response.status_code == 200, panel
        assert response.json()[key], panel

    assert (await client.get(f"{PREFIX}/runs/{run_id}/guardrails")).json()
    assert (await client.get(f"{PREFIX}/runs/{run_id}/approvals")).json()


async def test_events_are_paginated_by_seq(client: httpx.AsyncClient, run_id: str):
    """UI §50: never load the whole log at once."""
    first = (await client.get(f"{PREFIX}/runs/{run_id}/events", params={"limit": 3})).json()
    assert len(first["events"]) == 3
    assert first["next_seq"] == first["events"][-1]["seq"]

    second = (
        await client.get(
            f"{PREFIX}/runs/{run_id}/events",
            params={"limit": 3, "after_seq": first["next_seq"]},
        )
    ).json()
    assert second["events"][0]["seq"] > first["events"][-1]["seq"]


async def test_a_run_accepts_an_id_prefix(client: httpx.AsyncClient, run_id: str):
    response = await client.get(f"{PREFIX}/runs/{run_id[:12]}")
    assert response.status_code == 200
    assert response.json()["run"]["id"] == run_id


async def test_a_missing_run_explains_what_to_do(client: httpx.AsyncClient):
    """UI §48: errors are actionable, never "Error 500"."""
    response = await client.get(f"{PREFIX}/runs/run_does_not_exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "Run not found"
    assert "REWYN_HOME" in body["detail"]
    assert body["action"] == "Browse runs"
    assert body["href"] == "/runs"


async def test_the_overview_answers_whether_the_system_is_healthy(
    client: httpx.AsyncClient, run_id: str
):
    """UI §6."""
    overview = (await client.get(f"{PREFIX}/overview")).json()
    assert overview["status"] == "healthy"
    labels = [m["label"] for m in overview["metrics"]]
    assert labels == ["Success rate", "Regression", "Avg latency", "Avg cost", "Runs"]
    assert overview["recent_runs"][0]["id"] == run_id
    assert overview["incidents"] == []


async def test_search_groups_results_the_way_the_spec_prints_them(
    client: httpx.AsyncClient, run_id: str
):
    """UI §5."""
    results = (await client.get(f"{PREFIX}/search", params={"q": "salesforce"})).json()
    assert "MCP servers" in results["groups"]
    assert any(h["group"] == "MCP servers" for h in results["hits"])

    by_run = (await client.get(f"{PREFIX}/search", params={"q": run_id[:10]})).json()
    assert by_run["hits"][0]["group"] == "Runs"
    assert by_run["hits"][0]["href"] == f"/runs/{run_id}"


async def test_sessions_and_environments_back_the_navigation(
    client: httpx.AsyncClient, run_id: str
):
    """UI §4 and §42."""
    sessions = (await client.get(f"{PREFIX}/sessions")).json()
    assert sessions[0]["id"] == "sess-1"
    environments = (await client.get(f"{PREFIX}/environments")).json()
    assert environments[0]["name"] == "production"


async def test_export_returns_a_portable_bundle(client: httpx.AsyncClient, run_id: str):
    """UI §8: the Export action on the run header."""
    response = await client.get(f"{PREFIX}/runs/{run_id}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.content[:2] == b"PK"


async def test_the_local_console_refuses_requests_from_other_hosts(store: LocalStore):
    """The local surface serves one machine (UI §44, §54)."""
    app = build_app(store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://rewyn.example.com"
    ) as remote:
        response = await remote.get(f"{PREFIX}/health")
    assert response.status_code == 403
    assert response.json()["error"] == "Not reachable from this host"


async def test_an_unbuilt_frontend_says_how_to_build_it(store: LocalStore, tmp_path):
    """UI §47: an empty state teaches instead of 404ing."""
    app = build_app(store, static_dir=tmp_path / "missing")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as unbuilt:
        response = await unbuilt.get("/")
    assert response.status_code == 200
    assert "make build-web" in response.text


async def test_the_openapi_document_is_the_contract(client: httpx.AsyncClient):
    """UI §52: the UI is generated from this, so it must stay complete."""
    schema = (await client.get(f"{PREFIX}/openapi.json")).json()
    paths = set(schema["paths"])
    assert {
        f"{PREFIX}/runs",
        f"{PREFIX}/runs/{{run_id}}",
        f"{PREFIX}/runs/{{run_id}}/timeline",
        f"{PREFIX}/runs/{{run_id}}/context",
        f"{PREFIX}/overview",
        f"{PREFIX}/search",
    } <= paths


# The control room (UI §23, §36, §37, §45) ------------------------------------
async def failing_runs(store: LocalStore, count: int = 3) -> list[str]:
    """Record a cluster of identical failures, which is what an incident is."""
    from rewyn.core.run import start_run
    from rewyn.runtime.recorder import default_recorder

    ids: list[str] = []
    for index in range(count):
        try:
            async with start_run("refund-agent", input=f"case {index}") as run:
                ids.append(run.id)
                raise RuntimeError("refund policy requires manager approval")
        except RuntimeError:
            pass
    default_recorder().flush()
    return ids


async def test_a_failure_cluster_is_served_as_an_incident_with_its_timeline(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §36."""
    ids = await failing_runs(store)
    incidents = (await client.get(f"{PREFIX}/incidents")).json()
    assert incidents
    incident = incidents[0]
    assert incident["affected_runs"] == len(ids)
    assert set(incident["run_ids"]) == set(ids)
    assert [stage["stage"] for stage in incident["timeline"]] == [
        "deployment",
        "behavior_change",
        "detection",
        "investigation",
        "fix",
        "regression",
        "resolved",
    ]

    one = (await client.get(f"{PREFIX}/incidents/{incident['id']}")).json()
    assert one["id"] == incident["id"]
    assert one["runs"][0]["id"] in ids


async def test_an_incident_survives_a_restart_of_the_console(
    client: httpx.AsyncClient, store: LocalStore
):
    """Incidents are assigned and linked to, so the id has to be stable."""
    await failing_runs(store)
    first = (await client.get(f"{PREFIX}/incidents")).json()[0]["id"]

    fresh = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app(store)), base_url=BASE_URL
    )
    async with fresh:
        assert (await fresh.get(f"{PREFIX}/incidents")).json()[0]["id"] == first


async def test_an_incident_that_no_longer_exists_says_why(client: httpx.AsyncClient):
    response = await client.get(f"{PREFIX}/incidents/errors-deadbeef")
    assert response.status_code == 404
    assert response.json()["detail"]["href"] == "/incidents"


async def test_taking_an_incident_moves_it_along_its_own_timeline(
    client: httpx.AsyncClient, store: LocalStore
):
    """UI §36, §45: assignment is part of the timeline, not a label beside it."""
    await failing_runs(store)
    incident_id = (await client.get(f"{PREFIX}/incidents")).json()[0]["id"]

    updated = (
        await client.post(
            f"{PREFIX}/incidents/{incident_id}",
            json={"status": "investigating", "assignee": "raj", "note": "reproducing locally"},
        )
    ).json()
    assert updated["assignee"] == "raj"
    assert updated["status"] == "investigating"
    stage = next(s for s in updated["timeline"] if s["stage"] == "investigation")
    assert stage["reached"]
    assert "raj" in stage["summary"]


async def test_a_comment_thread_hangs_off_anything_the_console_can_open(
    client: httpx.AsyncClient, run_id: str
):
    """UI §45."""
    created = await client.post(
        f"{PREFIX}/comments", json={"subject": f"run:{run_id}", "body": "this is the slow one"}
    )
    assert created.status_code == 201
    comment = created.json()
    assert comment["author"]

    thread = (await client.get(f"{PREFIX}/comments", params={"subject": f"run:{run_id}"})).json()
    assert [c["id"] for c in thread] == [comment["id"]]

    resolved = (await client.post(f"{PREFIX}/comments/{comment['id']}/resolve")).json()
    assert resolved["resolved"] is True


async def test_a_comment_on_something_unopenable_is_refused_with_an_action(
    client: httpx.AsyncClient,
):
    """UI §48."""
    response = await client.post(
        f"{PREFIX}/comments", json={"subject": "spreadsheet:q3", "body": "hi"}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["action"] == "Browse runs"


async def test_a_saved_view_round_trips_and_can_be_removed(client: httpx.AsyncClient):
    """UI §45: a question asked once can be asked again."""
    created = await client.post(
        f"{PREFIX}/views",
        json={"name": "Failing refunds", "screen": "runs", "query": "?status=failed"},
    )
    assert created.status_code == 201
    view = created.json()

    listed = (await client.get(f"{PREFIX}/views", params={"screen": "runs"})).json()
    assert [v["id"] for v in listed] == [view["id"]]

    assert (await client.delete(f"{PREFIX}/views/{view['id']}")).status_code == 204
    assert (await client.get(f"{PREFIX}/views")).json() == []


async def test_explain_difference_answers_without_a_model_and_says_who_wrote_it(
    client: httpx.AsyncClient, run_id: str, store: LocalStore
):
    """UI §23, §44: an explanation must not need an API key."""
    from tests.ui.conftest import record_demo_run

    other = await record_demo_run(question="Should we cut Acme's credit limit?")
    response = await client.post(f"{PREFIX}/runs/{run_id}/explain", params={"against": other})
    assert response.status_code == 200
    narrative = response.json()
    assert narrative["author"] == "rules"
    assert narrative["grounded"] is True
    assert all(claim["evidence"] for claim in narrative["claims"])
    assert {claim["label"] for claim in narrative["claims"]} <= {"Observed", "Inference"}
