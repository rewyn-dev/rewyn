"""Incidents: a failed run, and everything that happened around it (UI spec §36).

UI §36 asks for four things -- when it started, how many runs it affected,
the likely cause, and the deployment → detection → fix → resolved timeline.
Three of those are computed from the runs themselves, so an incident exists
the moment the failures do; nobody has to declare one.

The fourth, the timeline, is where a product like this usually starts
guessing. It does not here. Each of the seven stages is returned either with
the recorded thing that reached it -- the dependency version that moved, the
run that first failed, the regression report that proved the fix -- or as
unreached, with a sentence saying what would reach it. An empty stage is
information: it is the step nobody has done yet.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from rewyn.core.types import utcnow
from rewyn.ui import schemas as s

# How many failures sharing one message make a cluster worth a page of its own.
MIN_CLUSTER = 3
COST_SPIKE_PERCENT = 15.0
DEFAULT_WINDOW = timedelta(hours=24)

STAGE_LABELS: dict[str, str] = {
    "deployment": "Deployment",
    "behavior_change": "Behavior change",
    "detection": "Detection",
    "investigation": "Investigation",
    "fix": "Fix",
    "regression": "Regression",
    "resolved": "Resolved",
}

# What each stage is waiting for, printed when nothing has reached it yet.
STAGE_WAITING: dict[str, str] = {
    "deployment": (
        "No dependency version moved before this started, so nothing was deployed into it."
    ),
    "behavior_change": "No affected run is recorded.",
    "detection": "Below the clustering threshold; this has not been detected as a pattern.",
    "investigation": "Nobody has taken this yet. Assign it, or leave a note on it.",
    "fix": "No dependency has changed since this started, so nothing has been shipped at it.",
    "regression": (
        "No regression report covers this agent since it started. "
        "Save a failing run as a test and run it."
    ),
    "resolved": "Still open: mark it resolved, or let a clean run of this agent close it.",
}


def incident_id(kind: str, key: str) -> str:
    """A stable, URL-safe id.

    Incidents are addressable -- they are assigned, commented on and linked
    to -- so the id has to survive a restart. ``hash()`` does not: Python
    salts string hashing per process, so the same failure cluster would get a
    new id every time the server came up.
    """
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
    return f"{kind}-{digest}"


def detect(
    runs: Sequence[s.RunSummary],
    *,
    window: timedelta = DEFAULT_WINDOW,
    now: datetime | None = None,
) -> list[s.Incident]:
    """Clusters worth looking at, each naming the runs that produced it (UI §6, §36)."""
    moment = now or utcnow()
    recent = [r for r in runs if (moment - r.started_at) <= window]
    older = [r for r in runs if (moment - r.started_at) > window]
    found: list[s.Incident] = []

    clusters: dict[str, list[s.RunSummary]] = {}
    for run in recent:
        if run.error:
            clusters.setdefault(run.error.split("\n")[0][:120], []).append(run)
    for message, cluster in sorted(clusters.items(), key=lambda kv: len(kv[1]), reverse=True):
        if len(cluster) < MIN_CLUSTER:
            continue
        agents = {r.agent for r in cluster if r.agent}
        environments = {r.environment for r in cluster if r.environment}
        found.append(
            s.Incident(
                id=incident_id("errors", message),
                severity="critical" if len(cluster) >= MIN_CLUSTER * 3 else "warning",
                summary=f"{len(cluster)} runs failing: {message}",
                detail=message,
                run_ids=[r.id for r in sorted(cluster, key=lambda r: r.started_at)],
                started_at=min(r.started_at for r in cluster),
                affected_runs=len(cluster),
                likely_cause=_shared_dependency(cluster),
                agent=next(iter(agents)) if len(agents) == 1 else None,
                environment=next(iter(environments)) if len(environments) == 1 else None,
            )
        )

    spike = _cost_spike(recent, older)
    if spike is not None:
        found.append(spike)
    return found


def _shared_dependency(cluster: Sequence[s.RunSummary]) -> str | None:
    agents = {r.agent for r in cluster if r.agent}
    if len(agents) == 1:
        return f"all failures are in {next(iter(agents))}"
    models = {r.model for r in cluster if r.model}
    if len(models) == 1:
        return f"all failures used {next(iter(models))}"
    return None


def _cost_spike(recent: Sequence[s.RunSummary], older: Sequence[s.RunSummary]) -> s.Incident | None:
    if not recent or not older:
        return None
    now_avg = sum(r.cost for r in recent) / len(recent)
    then_avg = sum(r.cost for r in older) / len(older)
    if then_avg <= 0 or now_avg <= then_avg:
        return None
    change = (now_avg - then_avg) / then_avg * 100.0
    if change < COST_SPIKE_PERCENT:
        return None
    agents = {r.agent for r in recent if r.agent}
    return s.Incident(
        id=incident_id("cost", "average-cost-per-run"),
        severity="warning",
        summary=f"Cost increased {change:.0f}%",
        detail=f"average cost per run moved from {then_avg:.4f} to {now_avg:.4f}",
        run_ids=[r.id for r in recent[:20]],
        started_at=min(r.started_at for r in recent),
        affected_runs=len(recent),
        likely_cause=None,
        agent=next(iter(agents)) if len(agents) == 1 else None,
    )


def _stage(
    key: str,
    *,
    at: datetime | None = None,
    summary: str = "",
    evidence: str = "",
    href: str | None = None,
) -> s.IncidentStage:
    reached = summary != ""
    return s.IncidentStage(
        stage=key,
        reached=reached,
        at=at if reached else None,
        summary=summary or STAGE_WAITING[key],
        evidence=evidence,
        href=href if reached else None,
    )


def timeline(
    incident: s.Incident,
    *,
    runs: Sequence[s.RunSummary],
    changes: Sequence[s.RecentChange] = (),
    reports: Sequence[Any] = (),
    state: Mapping[str, Any] | None = None,
    comments: Sequence[s.IncidentComment] = (),
    agent_runs: Sequence[s.RunSummary] = (),
) -> list[s.IncidentStage]:
    """The seven steps of UI §36, each with the evidence that reached it."""
    owner = state or {}
    status = str(owner.get("status") or "open")
    started = incident.started_at or (runs[0].started_at if runs else None)
    affected = sorted(runs, key=lambda r: r.started_at)
    agent = incident.agent

    mine = [c for c in changes if agent is None or c.name == agent or c.kind == "agent"]
    before = [c for c in mine if started is not None and c.at <= started]
    after = [c for c in mine if started is not None and c.at > started]

    deployment = max(before, key=lambda c: c.at) if before else None
    stages = [
        _stage(
            "deployment",
            at=deployment.at if deployment else None,
            summary=(
                f"{deployment.kind} {deployment.name} went from "
                f"{deployment.before or 'unversioned'} to {deployment.after or 'unversioned'}"
                if deployment
                else ""
            ),
            evidence=f"recorded in run {deployment.run_id}" if deployment else "",
            href=f"/runs/{deployment.run_id}" if deployment else "",
        )
    ]

    first = affected[0] if affected else None
    stages.append(
        _stage(
            "behavior_change",
            at=first.started_at if first else None,
            summary=(
                f"{first.agent or first.name} run {first.id} "
                f"{'failed' if first.error else 'changed'}"
                if first
                else ""
            ),
            evidence=(first.error.split("\n")[0][:160] if first and first.error else ""),
            href=f"/runs/{first.id}" if first else "",
        )
    )

    detected = affected[MIN_CLUSTER - 1] if len(affected) >= MIN_CLUSTER else None
    stages.append(
        _stage(
            "detection",
            at=detected.started_at if detected else None,
            summary=(f"{incident.affected_runs} runs matched the same failure" if detected else ""),
            evidence=f"clustered at {MIN_CLUSTER} or more occurrences" if detected else "",
            href="/notifications" if detected else "",
        )
    )

    note = str(owner.get("note") or "")
    first_comment = comments[0] if comments else None
    assignee = owner.get("assignee")
    investigation = ""
    evidence = ""
    at: datetime | None = None
    if assignee:
        investigation = f"assigned to {assignee}"
        evidence = note
    elif first_comment is not None:
        investigation = f"{first_comment.author} left a note"
        evidence = first_comment.body[:200]
        at = first_comment.created_at
    elif status in {"investigating", "mitigated", "resolved"}:
        investigation = f"marked {status}"
    stages.append(_stage("investigation", at=at, summary=investigation, evidence=evidence))

    fix = min(after, key=lambda c: c.at) if after else None
    stages.append(
        _stage(
            "fix",
            at=fix.at if fix else None,
            summary=(
                f"{fix.kind} {fix.name} moved to {fix.after or 'unversioned'} after this started"
                if fix
                else ("marked mitigated" if status in {"mitigated", "resolved"} else "")
            ),
            evidence=f"recorded in run {fix.run_id}" if fix else "",
            href=f"/runs/{fix.run_id}" if fix else "",
        )
    )

    report = _report_after(reports, agent, started)
    stages.append(
        _stage(
            "regression",
            at=_report_time(report),
            summary=(
                f"{report.dataset}: {report.passed}/{report.total} passing"
                if report is not None
                else ""
            ),
            evidence=("no regressions" if report is not None and not report.regressions else ""),
            href=f"/regression/{report.id}" if report is not None else "",
        )
    )

    clean = _clean_since(agent_runs, affected)
    stages.append(
        _stage(
            "resolved",
            at=_resolved_at(status, clean, owner),
            summary=(
                "closed by hand"
                if status == "resolved"
                else (
                    f"{len(clean)} clean runs of {agent} since the last affected one"
                    if clean
                    else ""
                )
            ),
            evidence=(f"latest is {clean[-1].id}" if clean else ""),
            href=(f"/runs/{clean[-1].id}" if clean else ""),
        )
    )
    return stages


def _report_after(reports: Sequence[Any], agent: str | None, started: datetime | None) -> Any:
    for report in reports:
        target = getattr(report, "target", "") or ""
        created = getattr(report, "created_at", None)
        if agent is not None and agent not in target:
            continue
        if started is not None and created is not None and created < started:
            continue
        return report
    return None


def _report_time(report: Any) -> datetime | None:
    return getattr(report, "created_at", None) if report is not None else None


def _clean_since(
    agent_runs: Sequence[s.RunSummary], affected: Sequence[s.RunSummary]
) -> list[s.RunSummary]:
    """Successful runs of the same agent newer than the last affected one."""
    if not affected:
        return []
    cutoff = max(r.started_at for r in affected)
    return sorted(
        (r for r in agent_runs if r.started_at > cutoff and not r.error),
        key=lambda r: r.started_at,
    )


def _resolved_at(
    status: str, clean: Sequence[s.RunSummary], owner: Mapping[str, Any]
) -> datetime | None:
    if status == "resolved":
        updated = owner.get("updated_at")
        if isinstance(updated, str):
            try:
                return datetime.fromisoformat(updated)
            except ValueError:
                return None
        return updated if isinstance(updated, datetime) else None
    return clean[-1].started_at if clean else None


def detail(
    incident: s.Incident,
    *,
    runs: Sequence[s.RunSummary],
    changes: Sequence[s.RecentChange] = (),
    reports: Sequence[Any] = (),
    state: Mapping[str, Any] | None = None,
    comments: Sequence[s.IncidentComment] = (),
    agent_runs: Sequence[s.RunSummary] = (),
) -> s.IncidentDetail:
    """One incident, with its timeline and everyone working on it (UI §36)."""
    owner = dict(state or {})
    affected = sorted(runs, key=lambda r: r.started_at)
    stages = timeline(
        incident,
        runs=affected,
        changes=changes,
        reports=reports,
        state=owner,
        comments=comments,
        agent_runs=agent_runs,
    )
    resolved = next(s for s in stages if s.stage == "resolved")
    status = str(owner.get("status") or ("resolved" if resolved.reached else "open"))
    return s.IncidentDetail(
        id=incident.id,
        severity=incident.severity,
        summary=incident.summary,
        detail=incident.detail,
        run_ids=incident.run_ids,
        started_at=incident.started_at,
        affected_runs=incident.affected_runs,
        likely_cause=incident.likely_cause,
        agent=incident.agent,
        environment=incident.environment,
        status=status,
        assignee=owner.get("assignee"),
        note=str(owner.get("note") or ""),
        first_seen=affected[0].started_at if affected else incident.started_at,
        last_seen=affected[-1].started_at if affected else incident.started_at,
        cause_evidence=_cause_evidence(incident, stages),
        timeline=stages,
        runs=affected,
        comments=list(comments),
    )


def _cause_evidence(incident: s.Incident, stages: Sequence[s.IncidentStage]) -> list[str]:
    """Why the likely cause is believed, one line per piece of evidence (UI §23)."""
    evidence: list[str] = []
    if incident.likely_cause:
        evidence.append(f"Observed: {incident.likely_cause}.")
    deployment = next((s for s in stages if s.stage == "deployment"), None)
    if deployment is not None and deployment.reached:
        evidence.append(
            f"Observed: {deployment.summary}, before the first affected run "
            f"({deployment.evidence})."
        )
        evidence.append(
            "Inference: that change is the only recorded thing that moved before this "
            "started, so it is the strongest candidate — not a confirmed cause."
        )
    elif incident.affected_runs:
        evidence.append(
            "Observed: no dependency version moved before this started, so this is not "
            "explained by anything Rewyn recorded."
        )
    return evidence
