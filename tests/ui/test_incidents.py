"""Incidents: detection, and a timeline that only claims what happened (UI spec §36)."""

from __future__ import annotations

from datetime import timedelta

from rewyn.core.types import utcnow
from rewyn.ui import incidents
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


def change(**kwargs: object) -> s.RecentChange:
    base: dict[str, object] = {
        "at": utcnow(),
        "kind": "skill",
        "name": "refund-agent",
        "before": "18",
        "after": "19",
        "run_id": "run_9",
        "summary": "refund-agent 18 → 19",
    }
    base.update(kwargs)
    return s.RecentChange.model_validate(base)


def test_a_failure_cluster_becomes_an_incident_that_names_its_runs():
    """UI §6 and §36: evidence first."""
    now = utcnow()
    runs = [
        summary(
            id=f"run_{i}",
            status="failed",
            error="ToolError: salesforce timeout",
            started_at=now - timedelta(minutes=5 - i),
        )
        for i in range(5)
    ]
    found = incidents.detect(runs, now=now)
    assert found
    incident = found[0]
    assert incident.affected_runs == 5
    assert incident.run_ids == [r.id for r in runs]
    assert "salesforce timeout" in incident.summary
    assert incident.likely_cause == "all failures are in refund-agent"
    assert incident.agent == "refund-agent"


def test_a_single_failure_is_not_an_incident():
    now = utcnow()
    runs = [summary(id="run_1", status="failed", error="boom", started_at=now)]
    assert incidents.detect(runs, now=now) == []


def test_a_cost_increase_is_reported_with_the_numbers_behind_it():
    now = utcnow()
    recent = [summary(id=f"new_{i}", cost=0.12, started_at=now) for i in range(4)]
    older = [
        summary(id=f"old_{i}", cost=0.08, started_at=now - timedelta(days=2)) for i in range(4)
    ]
    found = incidents.detect([*recent, *older], now=now)
    spike = next(i for i in found if i.id.startswith("cost-"))
    assert "50%" in spike.summary
    assert "0.0800" in spike.detail


def test_the_id_survives_a_restart():
    """An incident is assigned and linked to, so its id cannot be a process hash."""
    assert incidents.incident_id("errors", "ToolError: boom") == incidents.incident_id(
        "errors", "ToolError: boom"
    )
    assert "/" not in incidents.incident_id("errors", "a/b c")


def test_the_timeline_prints_all_seven_stages_even_when_nothing_reached_them():
    """UI §36 draws seven steps; a missing one is information, not a gap."""
    now = utcnow()
    runs = [summary(id=f"run_{i}", status="failed", error="boom", started_at=now) for i in range(3)]
    incident = incidents.detect(runs, now=now)[0]
    stages = incidents.timeline(incident, runs=runs)
    assert [stage.stage for stage in stages] == list(s.INCIDENT_STAGES)
    unreached = [stage.stage for stage in stages if not stage.reached]
    assert "deployment" in unreached
    assert "regression" in unreached
    # An unreached stage says what would reach it, rather than going blank.
    assert all(stage.summary for stage in stages)


def test_a_deployment_before_the_first_failure_reaches_the_deployment_stage():
    now = utcnow()
    runs = [
        summary(
            id=f"run_{i}", status="failed", error="boom", started_at=now - timedelta(minutes=10)
        )
        for i in range(3)
    ]
    incident = incidents.detect(runs, now=now)[0]
    deployed = change(at=now - timedelta(hours=1))
    stages = incidents.timeline(incident, runs=runs, changes=[deployed])
    stage = next(s for s in stages if s.stage == "deployment")
    assert stage.reached
    assert "18" in stage.summary
    assert "19" in stage.summary
    assert stage.href == "/runs/run_9"


def test_a_change_after_the_incident_started_is_a_fix_not_a_deployment():
    now = utcnow()
    started = now - timedelta(hours=2)
    runs = [
        summary(id=f"run_{i}", status="failed", error="boom", started_at=started) for i in range(3)
    ]
    incident = incidents.detect(runs, now=now)[0]
    stages = incidents.timeline(
        incident, runs=runs, changes=[change(at=now - timedelta(minutes=30), after="20")]
    )
    assert not next(s for s in stages if s.stage == "deployment").reached
    fix = next(s for s in stages if s.stage == "fix")
    assert fix.reached
    assert "20" in fix.summary


def test_clean_runs_after_the_last_failure_resolve_it():
    now = utcnow()
    started = now - timedelta(hours=3)
    failing = [
        summary(id=f"bad_{i}", status="failed", error="boom", started_at=started) for i in range(3)
    ]
    healthy = [summary(id=f"ok_{i}", started_at=now - timedelta(minutes=10 - i)) for i in range(3)]
    incident = incidents.detect([*failing, *healthy], now=now)[0]
    detail = incidents.detail(incident, runs=failing, agent_runs=[*failing, *healthy])
    resolved = next(s for s in detail.timeline if s.stage == "resolved")
    assert resolved.reached
    assert "3 clean runs" in resolved.summary
    assert detail.status == "resolved"


def test_an_assignee_reaches_investigation_and_is_carried_into_the_detail():
    now = utcnow()
    runs = [summary(id=f"run_{i}", status="failed", error="boom", started_at=now) for i in range(3)]
    incident = incidents.detect(runs, now=now)[0]
    detail = incidents.detail(
        incident,
        runs=runs,
        state={"status": "investigating", "assignee": "raj", "note": "reproducing locally"},
    )
    assert detail.assignee == "raj"
    assert detail.status == "investigating"
    stage = next(s for s in detail.timeline if s.stage == "investigation")
    assert stage.reached
    assert "raj" in stage.summary
    assert stage.evidence == "reproducing locally"


def test_the_cause_is_labelled_observed_or_inference_and_never_asserted():
    """UI §23's rule, applied to a cause (UI §36)."""
    now = utcnow()
    runs = [
        summary(id=f"run_{i}", status="failed", error="boom", started_at=now - timedelta(minutes=5))
        for i in range(3)
    ]
    incident = incidents.detect(runs, now=now)[0]
    detail = incidents.detail(incident, runs=runs, changes=[change(at=now - timedelta(hours=1))])
    assert detail.cause_evidence
    assert all(line.startswith(("Observed:", "Inference:")) for line in detail.cause_evidence)
    inference = next(line for line in detail.cause_evidence if line.startswith("Inference:"))
    assert "not a confirmed cause" in inference


def test_without_a_deployment_the_detail_says_nothing_explains_it():
    now = utcnow()
    runs = [summary(id=f"run_{i}", status="failed", error="boom", started_at=now) for i in range(3)]
    incident = incidents.detect(runs, now=now)[0]
    detail = incidents.detail(incident, runs=runs)
    assert any("not explained by anything Rewyn recorded" in c for c in detail.cause_evidence)
