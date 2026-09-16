"""Dependencies, drift, cost, releases and experiments (UI spec §28, §31-§33, §40, §43).

These are the screens that answer "what could have changed outside my code?"
and "is it safe to deploy?", so every one of them is built from evidence the
runs already carry. Nothing here estimates, models or predicts: a drift cause
is a dependency whose recorded version moved, a release is blocked because a
gate actually failed, and a cost slice is the sum of what was spent.

Where the evidence runs out, that is said plainly rather than filled in --
UI §32 asks for drift to be evidence-based, and the most useful thing a drift
page can report is that behaviour moved while nothing underneath it did.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from rewyn.core.manifest import BehaviorManifest, DependencyGraph, DriftKind, detect_drift
from rewyn.core.run import DependencyRef, RunManifest
from rewyn.core.types import utcnow
from rewyn.ui import schemas as s

# Where a dependency kind is browsable in the console (UI §31: clickable).
HREFS: dict[str, str] = {
    "agent": "/agents/{name}",
    "graph": "/graphs/{name}",
    "model": "/models/{name}",
    "embedding_model": "/models/{name}",
    "prompt": "/prompts/{name}",
    "skill": "/skills/{name}",
    "tool": "/tools/{name}",
    "mcp_server": "/mcp/{name}",
    "context": "/context/{name}",
    "memory": "/memory/{name}",
    "dataset": "/datasets/{name}",
}

# The order §31's picture hangs dependencies off the root in.
KIND_ORDER: tuple[str, ...] = (
    "model",
    "prompt",
    "skill",
    "tool",
    "mcp_server",
    "memory",
    "context",
    "retriever",
    "guardrail",
    "embedding_model",
    "dataset",
    "evaluator",
    "sandbox",
)

ROOT_KINDS = ("graph", "agent")

ReleaseStatus = Literal["ready", "blocked", "unverified"]


def release_id(application: str, version: str) -> str:
    """``refund-agent@3``. Stable, so a release can be linked to and promoted."""
    return f"{application}@{version}"


def split_release_id(value: str) -> tuple[str, str]:
    application, _, version = value.rpartition("@")
    if not application or not version:
        raise ValueError(f"{value!r} is not a release id; the form is <application>@<version>")
    return application, version


def _node_id(dependency: DependencyRef) -> str:
    return f"{dependency.kind}:{dependency.name}"


def _href(dependency: DependencyRef) -> str | None:
    template = HREFS.get(dependency.kind)
    return template.format(name=dependency.name) if template else None


def dependency_map(
    agent: str,
    manifests: Sequence[RunManifest],
    *,
    owned: Mapping[str, Sequence[str]] | None = None,
) -> s.DependencyMap:
    """What an agent depends on, and what those depend on (UI §31).

    Runs record dependencies as a flat set, so the second level is drawn only
    where a relationship was actually observed: the tools an MCP server
    exposed, and the tools a skill declared. Anything else hangs off the root,
    which is the truth rather than an invented hierarchy.
    """
    graph = DependencyGraph.from_manifests(list(manifests))
    ownership = {owner: set(names) for owner, names in (owned or {}).items()}
    root_ref = next(
        (d for kind in ROOT_KINDS for d in graph.nodes if d.kind == kind and d.name == agent),
        None,
    )
    root_id = _node_id(root_ref) if root_ref else f"agent:{agent}"

    nodes: list[s.DependencyNode] = [
        s.DependencyNode(
            id=root_id,
            kind=root_ref.kind if root_ref else "agent",
            name=agent,
            version=root_ref.version if root_ref else "unversioned",
            runs=len(manifests),
            href=f"/agents/{agent}",
        )
    ]
    edges: list[s.DependencyEdge] = []
    claimed: set[str] = set()

    for owner, names in ownership.items():
        for dependency in graph.nodes:
            if dependency.kind == "tool" and dependency.name in names:
                claimed.add(_node_id(dependency))
                edges.append(
                    s.DependencyEdge(source=owner, target=_node_id(dependency), relation="exposes")
                )

    ordered = sorted(
        (d for d in graph.nodes if _node_id(d) != root_id),
        key=lambda d: (KIND_ORDER.index(d.kind) if d.kind in KIND_ORDER else 99, d.name),
    )
    for dependency in ordered:
        node_id = _node_id(dependency)
        nodes.append(
            s.DependencyNode(
                id=node_id,
                kind=dependency.kind,
                name=dependency.name,
                version=dependency.version,
                href=_href(dependency),
            )
        )
        if node_id not in claimed:
            edges.append(s.DependencyEdge(source=root_id, target=node_id))

    return s.DependencyMap(
        root=root_id,
        agent=agent,
        runs=len(manifests),
        nodes=nodes,
        edges=edges,
        fingerprint=graph.fingerprint(),
    )


def mcp_tool_ownership(payloads: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """``mcp_server:<name> -> [tool names]``, from discovery events."""
    owned: dict[str, list[str]] = {}
    for payload in payloads:
        server = str(payload.get("server", ""))
        if not server:
            continue
        names = [
            str(tool.get("name"))
            for tool in payload.get("tools") or []
            if isinstance(tool, dict) and tool.get("name")
        ]
        owned.setdefault(f"mcp_server:{server}", []).extend(names)
    return owned


def skill_tool_ownership(payloads: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """``skill:<name> -> [allowed tools]``, from skill loads."""
    owned: dict[str, list[str]] = {}
    for payload in payloads:
        skill = str(payload.get("skill", ""))
        allowed = [str(name) for name in payload.get("allowed_tools") or []]
        if skill and allowed:
            owned.setdefault(f"skill:{skill}", []).extend(allowed)
    return owned


# Drift ------------------------------------------------------------------------
def _rate(runs: Sequence[s.RunSummary]) -> float:
    if not runs:
        return 0.0
    return sum(1 for r in runs if r.status == "succeeded") / len(runs) * 100.0


def _mean_cost(runs: Sequence[s.RunSummary]) -> float:
    return (sum(r.cost for r in runs) / len(runs)) if runs else 0.0


def drift_view(
    agent: str,
    baseline: Sequence[RunManifest],
    current: Sequence[RunManifest],
    *,
    baseline_runs: Sequence[s.RunSummary],
    current_runs: Sequence[s.RunSummary],
) -> s.DriftView:
    """Behaviour drift for one agent, with the evidence for each cause (UI §32)."""
    before = BehaviorManifest.from_runs(agent, list(baseline), version="baseline")
    after = BehaviorManifest.from_runs(agent, list(current), version="current")
    report = detect_drift(before, after)

    causes = [
        s.DriftCause(
            kind=finding.kind.value,
            dependency_kind=finding.dependency_kind,
            name=finding.name,
            before=finding.before,
            after=finding.after,
            detail=finding.likely_cause,
        )
        for finding in report.findings
        if finding.kind is not DriftKind.UNEXPLAINED
    ]
    changed = {f.name for f in report.findings}
    unchanged = sorted({e.name for e in after.entries()} - changed)

    expected = _rate(baseline_runs)
    observed = _rate(current_runs)
    moved = bool(baseline_runs and current_runs) and abs(observed - expected) >= 1.0
    if moved and not causes:
        causes.append(
            s.DriftCause(
                kind="unexplained",
                detail=(
                    "Behaviour moved while every recorded dependency stayed identical. "
                    "The cause is outside the manifest: a provider update, external data, "
                    "or model nondeterminism."
                ),
                changed=False,
            )
        )

    return s.DriftView(
        agent=agent,
        baseline_runs=len(baseline_runs),
        current_runs=len(current_runs),
        baseline_from=min((r.started_at for r in baseline_runs), default=None),
        current_from=min((r.started_at for r in current_runs), default=None),
        expected_success=expected,
        current_success=observed,
        expected_cost=_mean_cost(baseline_runs),
        current_cost=_mean_cost(current_runs),
        drifted=bool(causes),
        silent=moved and not report.findings,
        causes=causes,
        unchanged=unchanged,
    )


# Cost -------------------------------------------------------------------------
def cost_view(
    group_by: str,
    rows: Sequence[tuple[str, float, int, int]],
    categories: Mapping[str, float],
    *,
    currency: str = "USD",
) -> s.CostView:
    """Cost by one dimension, with the number that matters most (UI §33).

    Cost per successful task, not cost per run: a cheap run that failed is not
    a saving.
    """
    total = float(categories.get("total", 0.0)) or sum(row[1] for row in rows)
    runs = sum(row[2] for row in rows)
    succeeded = sum(row[3] for row in rows)
    slices = [
        s.CostSlice(
            key=key,
            total=spend,
            runs=count,
            succeeded=ok,
            per_run=(spend / count) if count else 0.0,
            per_successful_task=(spend / ok) if ok else None,
            share=(spend / total * 100.0) if total else 0.0,
        )
        for key, spend, count, ok in rows
    ]
    return s.CostView(
        group_by=group_by,
        total=total,
        runs=runs,
        succeeded=succeeded,
        per_successful_task=(total / succeeded) if succeeded else None,
        categories=s.CostBreakdown(
            model=float(categories.get("model", 0.0)),
            tool=float(categories.get("tool", 0.0)),
            embedding=float(categories.get("embedding", 0.0)),
            retrieval=float(categories.get("retrieval", 0.0)),
            sandbox=float(categories.get("sandbox", 0.0)),
            total=total,
            currency=currency,
        ),
        slices=slices,
        currency=currency,
    )


# Releases ---------------------------------------------------------------------
def release_view(
    application: str,
    version: str,
    *,
    previous_version: str | None,
    manifests: Sequence[RunManifest],
    previous_manifests: Sequence[RunManifest],
    runs: Sequence[s.RunSummary],
    report: Any | None,
    baseline_report: Any | None = None,
) -> s.ReleaseView:
    """One version, and whether it is safe to deploy (UI §43).

    A version is "ready" only when a regression report says so. Without one it
    is unverified, which is a different thing from safe -- so the console says
    unverified and refuses to pretend.
    """
    changed: list[s.ReleaseComponent] = []
    if previous_manifests:
        before = BehaviorManifest.from_runs(
            application, list(previous_manifests), version=previous_version or "previous"
        )
        after = BehaviorManifest.from_runs(application, list(manifests), version=version)
        changed = [
            s.ReleaseComponent(
                kind=finding.dependency_kind,
                name=finding.name,
                before=finding.before,
                after=finding.after,
            )
            for finding in detect_drift(before, after).findings
        ]

    tests = report.total if report is not None else 0
    passed = report.succeeded if report is not None else 0
    blocked_by = (
        [gate.describe() for gate in report.gates if not gate.passed] if report is not None else []
    )
    status: ReleaseStatus
    if report is None:
        status = "unverified"
    elif report.passed and not blocked_by:
        status = "ready"
    else:
        status = "blocked"
        if not blocked_by:
            blocked_by = [f"{len(report.failures)} of {tests} cases failed"]

    cost_delta = None
    if report is not None and baseline_report is not None and baseline_report.avg_cost > 0:
        cost_delta = (report.avg_cost - baseline_report.avg_cost) / baseline_report.avg_cost * 100.0

    return s.ReleaseView(
        id=release_id(application, version),
        application=application,
        version=version,
        previous_version=previous_version,
        created_at=min((r.started_at for r in runs), default=None),
        runs=len(runs),
        changed=changed,
        tests=tests,
        passed=passed,
        failed=tests - passed,
        success_rate=(report.success_rate * 100.0) if report is not None else None,
        cost_delta_percent=cost_delta,
        report_id=report.id if report is not None else None,
        status=status,
        blocked_by=blocked_by,
        promotion=(
            "Promoting records the decision and the evidence behind it. Deploying is "
            "still your pipeline's job: it reads the decision rather than guessing."
        ),
    )


def refuse_promotion(release: s.ReleaseView) -> str | None:
    """Why this version may not be promoted, or ``None`` if it may (UI §43).

    The check lives here, not in either server, because "is it safe to
    deploy?" must have exactly one answer whichever surface is asked. A
    blocked release names the gate that failed; an unverified one is not the
    same as a safe one, and saying so is the whole point of the screen.
    """
    if release.status == "blocked":
        reasons = "; ".join(release.blocked_by) or "its regression gate failed"
        return f"{release.application} v{release.version} is blocked: {reasons}."
    if release.status == "unverified":
        return (
            f"{release.application} v{release.version} has no regression report, so "
            "nothing has shown it is safe. Unverified is not the same as ready."
        )
    return None


def promotion_record(
    release: s.ReleaseView, request: s.PromoteRequest, *, by: str
) -> s.PromotionRecord:
    """The decision itself, carrying the evidence it was made on."""
    return s.PromotionRecord(
        release_id=release.id,
        application=release.application,
        version=release.version,
        environment=request.environment or "production",
        by=request.by or by,
        at=utcnow(),
        report_id=release.report_id,
        note=request.note,
    )


# Experiments ------------------------------------------------------------------
def experiment_view(dataset: str, reports: Sequence[Any]) -> s.ExperimentView:
    """Control against variants on one dataset (UI §28).

    The winner is the arm with the highest success rate, and ties are broken
    by cost -- stated as a label, never as advice the console cannot support.
    """
    variants: list[s.VariantView] = []
    for index, report in enumerate(sorted(reports, key=lambda r: r.created_at)):
        variants.append(
            s.VariantView(
                label="Control" if index == 0 else f"Variant {chr(ord('A') + index - 1)}",
                report_id=report.id,
                target=report.target,
                tests=report.total,
                success_rate=report.success_rate * 100.0,
                avg_cost=report.avg_cost,
                avg_latency_ms=report.avg_latency_ms,
                failures=len(report.failures),
                metrics={metric.name: metric.mean for metric in report.metrics},
            )
        )
    if variants:
        best = max(variants, key=lambda v: (v.success_rate, -v.avg_cost))
        for variant in variants:
            if variant.report_id == best.report_id:
                variant.winner = True
    measured = sorted({name for variant in variants for name in variant.metrics})
    return s.ExperimentView(dataset=dataset, variants=variants, measured=measured)


# Notifications ----------------------------------------------------------------
def notifications(
    *,
    reports: Sequence[Any] = (),
    drift: Sequence[s.DriftView] = (),
    incidents: Sequence[s.Incident] = (),
    dependency_changes: Sequence[s.RecentChange] = (),
    now: datetime | None = None,
) -> list[s.NotificationView]:
    """The meaningful events UI §40 lists, and nothing else.

    Each one is deduplicated by cause rather than by occurrence: a dependency
    that changed once is one notification, however many runs saw it.
    """
    found: list[s.NotificationView] = []

    for report in reports:
        if report.passed:
            continue
        delta = report.success_delta()
        regressed = delta is not None and delta < 0
        found.append(
            s.NotificationView(
                id=f"regression:{report.id}",
                kind="regression" if regressed else "evaluation",
                severity="critical" if regressed else "warning",
                summary=(
                    f"Regression on {report.dataset}: success {report.success_rate:.0%}"
                    + (f" ({delta:+.0%})" if delta is not None else "")
                    if regressed
                    else f"Evaluation failed on {report.dataset}"
                ),
                detail=", ".join(g.describe() for g in report.gates if not g.passed),
                at=report.created_at,
                href=f"/regression/{report.id}",
            )
        )

    for view in drift:
        if not view.drifted and not view.silent:
            continue
        found.append(
            s.NotificationView(
                id=f"drift:{view.agent}",
                kind="drift",
                severity="warning",
                summary=(
                    f"{view.agent} drifted: success {view.expected_success:.0f}% → "
                    f"{view.current_success:.0f}%"
                ),
                detail=view.causes[0].detail if view.causes else "",
                at=view.current_from or (now or datetime.now().astimezone()),
                href=f"/drift?agent={view.agent}",
            )
        )

    for change in dependency_changes:
        if change.kind not in ("mcp_server", "mcp", "model", "skill", "prompt"):
            continue
        found.append(
            s.NotificationView(
                id=f"dependency:{change.kind}:{change.name}:{change.after}",
                kind="dependency",
                severity="info",
                summary=f"{change.kind} {change.summary}",
                detail=f"first seen in run {change.run_id}",
                at=change.at,
                href=f"/runs/{change.run_id}",
            )
        )

    for incident in incidents:
        started = incident.started_at
        if started is None:
            continue
        found.append(
            s.NotificationView(
                id=f"incident:{incident.id}",
                kind="cost" if incident.id.startswith("cost") else "failures",
                severity=incident.severity,
                summary=incident.summary,
                detail=incident.detail,
                at=started,
                href=f"/runs/{incident.run_ids[0]}" if incident.run_ids else "/runs",
            )
        )

    seen: set[str] = set()
    unique: list[s.NotificationView] = []
    for notification in sorted(found, key=lambda n: n.at, reverse=True):
        if notification.id in seen:
            continue
        seen.add(notification.id)
        unique.append(notification)
    return unique
