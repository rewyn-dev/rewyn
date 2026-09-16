"""The overview and global search: answers, not charts (UI spec §5, §6, §46)."""

from __future__ import annotations

from datetime import timedelta

from rewyn.core.types import utcnow
from rewyn.ui import aggregates
from rewyn.ui import schemas as s


def summary(**kwargs: object) -> s.RunSummary:
    base: dict[str, object] = {
        "id": "run_1",
        "name": "refund",
        "status": "succeeded",
        "started_at": utcnow(),
        "agent": "refund-agent",
    }
    base.update(kwargs)
    return s.RunSummary.model_validate(base)


def test_health_is_backed_by_the_failure_rate():
    assert aggregates.health({"runs": 0}) == "unknown"
    assert aggregates.health({"runs": 100, "failed": 1}) == "healthy"
    assert aggregates.health({"runs": 100, "failed": 9}) == "degraded"
    assert aggregates.health({"runs": 100, "failed": 40}) == "unhealthy"


def test_the_overview_prints_the_five_numbers_the_spec_lists():
    metrics = aggregates.metrics(
        {"success_rate": 96.2, "avg_latency_ms": 2400.0, "avg_cost": 0.08, "runs": 18291.0}
    )
    assert [m.label for m in metrics] == [
        "Success rate",
        "Regression",
        "Avg latency",
        "Avg cost",
        "Runs",
    ]
    assert metrics[2].value == 2.4
    assert metrics[2].unit == "seconds"


def test_recent_changes_report_observed_version_transitions():
    """UI §6: "Refund policy context updated" is a fact, not a guess."""
    now = utcnow()
    newer = summary(id="run_2", started_at=now)
    older = summary(id="run_1", started_at=now - timedelta(hours=2))
    changes = aggregates.recent_changes(
        [newer, older],
        {
            "run_2": {"skill:refund-policy": "19", "model:gpt-x": "1"},
            "run_1": {"skill:refund-policy": "18", "model:gpt-x": "1"},
        },
    )
    assert len(changes) == 1
    change = changes[0]
    assert (change.kind, change.name, change.before, change.after) == (
        "skill",
        "refund-policy",
        "18",
        "19",
    )
    assert change.run_id == "run_2"


def test_search_groups_appear_in_the_order_the_spec_prints_them():
    """UI §5."""
    results = aggregates.search(
        "refund",
        runs=[summary(id="run_18293", user="refund-bot")],
        agents=[("refund-agent", "18")],
        skills=[("refund-policy", "4")],
        datasets=[("refund-regression", "1")],
    )
    assert results.groups == ["Runs", "Agents", "Users", "Skills", "Datasets"]
    assert results.hits[0].group == "Runs"
    assert any(h.label == "refund-regression" for h in results.hits)


def test_search_ignores_an_empty_query():
    assert aggregates.search("  ", runs=[]).total == 0
