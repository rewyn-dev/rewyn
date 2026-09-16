"""The BUILD pages, agent pages and quality views (UI §26-§30).

"What is my AI made of?" is answerable without any new bookkeeping: every run
already records the dependencies it used, with kind, name, version and
fingerprint (SDK §34). Rolling those up by name gives the registry pages, and
rolling them up by version gives the agent version history.

Evaluations and regressions are the same idea applied to verdicts: the
evaluation subsystem already writes scores and regression reports, so the
console reads them rather than inventing a second source of truth.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from rewyn.core.manifest import BehaviorManifest, DriftReport
from rewyn.ui import schemas as s

# Which dependency kinds each BUILD page shows (UI §4).
REGISTRY_KINDS: dict[str, tuple[str, ...]] = {
    "agents": ("agent", "graph"),
    "models": ("model", "embedding_model"),
    "prompts": ("prompt",),
    "skills": ("skill",),
    "tools": ("tool",),
    "mcp": ("mcp_server", "mcp"),
    "context": ("context",),
    "memory": ("memory",),
    "graphs": ("graph",),
}

SCORE_BUCKETS = 10


def registry_entry(
    kind: str,
    name: str,
    versions: Sequence[s.RegistryVersion],
) -> s.RegistryEntry:
    latest = max(versions, key=lambda v: (v.last_seen or datetime.min, v.version), default=None)
    return s.RegistryEntry(
        kind=kind,
        name=name,
        version=latest.version if latest else "unversioned",
        versions=len(versions),
        runs=sum(v.runs for v in versions),
        last_used_at=max((v.last_seen for v in versions if v.last_seen), default=None),
    )


def agent_summary(
    name: str,
    *,
    kind: str,
    versions: Sequence[s.RegistryVersion],
    runs: Sequence[s.RunSummary],
    dependencies: Sequence[s.DependencyView],
) -> s.AgentSummary:
    """What an agent is, derived from what its runs actually used (UI §29)."""
    succeeded = sum(1 for r in runs if r.status == "succeeded")
    scored = [r.eval_score for r in runs if r.eval_score is not None]
    latest = max(versions, key=lambda v: (v.last_seen or datetime.min, v.version), default=None)
    return s.AgentSummary(
        name=name,
        kind=kind,
        version=latest.version if latest else "unversioned",
        versions=len(versions),
        environments=sorted({r.environment for r in runs}),
        runs=len(runs),
        success_rate=(succeeded / len(runs) * 100.0) if runs else 0.0,
        avg_cost=(sum(r.cost for r in runs) / len(runs)) if runs else 0.0,
        avg_latency_ms=(sum(r.duration_ms for r in runs) / len(runs)) if runs else 0.0,
        eval_score=(sum(scored) / len(scored)) if scored else None,
        model=next((d.name for d in dependencies if d.kind == "model"), None),
        tools=sum(1 for d in dependencies if d.kind == "tool"),
        skills=[d.name for d in dependencies if d.kind == "skill"],
        mcp_servers=[d.name for d in dependencies if d.kind in ("mcp_server", "mcp")],
        memory=any(d.kind == "memory" for d in dependencies),
        last_run_at=max((r.started_at for r in runs), default=None),
    )


def manifest_diff(before: BehaviorManifest, after: BehaviorManifest) -> s.ManifestDiff:
    """v17 → v18: exactly what changed underneath (UI §30)."""
    from rewyn.core.manifest import detect_drift

    report: DriftReport = detect_drift(before, after)
    changed = {f.name for f in report.findings}
    return s.ManifestDiff(
        application=after.application,
        before_version=before.version,
        after_version=after.version,
        findings=[
            s.DriftFindingView(
                kind=finding.kind.value,
                dependency_kind=finding.dependency_kind,
                name=finding.name,
                before=finding.before,
                after=finding.after,
                # A finding identifies a dependency by fingerprint when it has
                # one; §30 asks the screen to say "v17 → v18", so the versions
                # are looked up alongside it.
                before_version=_version_of(before, finding.dependency_kind, finding.name),
                after_version=_version_of(after, finding.dependency_kind, finding.name),
                likely_cause=finding.likely_cause,
            )
            for finding in report.findings
        ],
        unchanged=sum(1 for entry in after.entries() if entry.name not in changed),
    )


def _version_of(manifest: BehaviorManifest, kind: str, name: str) -> str | None:
    entry = manifest.get(kind, name)
    return entry.version if entry is not None else None


def distribution(values: Sequence[float], buckets: int = SCORE_BUCKETS) -> list[s.ScoreBucket]:
    """A histogram over ``0..1``: UI §27 asks for score distributions."""
    width = 1.0 / buckets
    counts = [0] * buckets
    for value in values:
        index = min(int(max(value, 0.0) / width), buckets - 1)
        counts[index] += 1
    return [
        s.ScoreBucket(lower=round(i * width, 2), upper=round((i + 1) * width, 2), count=count)
        for i, count in enumerate(counts)
    ]


def evaluator_summary(
    name: str, values: Sequence[float], passed: Sequence[bool], last: datetime | None
) -> s.EvaluatorSummary:
    return s.EvaluatorSummary(
        name=name,
        scores=len(values),
        mean=(sum(values) / len(values)) if values else 0.0,
        pass_rate=(sum(1 for p in passed if p) / len(passed) * 100.0) if passed else 0.0,
        distribution=distribution(values),
        last_scored_at=last,
    )


# Regression reports -----------------------------------------------------------
def report_summary(report: Any) -> s.RegressionSummary:
    return s.RegressionSummary(
        id=report.id,
        dataset=report.dataset,
        dataset_version=report.dataset_version,
        target=report.target,
        created_at=report.created_at,
        tests=report.total,
        succeeded=report.succeeded,
        success_rate=report.success_rate * 100.0,
        success_delta=(None if report.success_delta() is None else report.success_delta() * 100.0),
        avg_cost=report.avg_cost,
        avg_latency_ms=report.avg_latency_ms,
        passed=report.passed,
        baseline_id=report.baseline_id,
    )


def _case_view(case: Any, *, regressed: bool = False) -> s.RegressionCaseView:
    return s.RegressionCaseView(
        item_id=case.item_id,
        run_id=case.run_id,
        passed=case.passed,
        output=case.output,
        expected=case.expected,
        cost=case.cost,
        latency_ms=case.latency_ms,
        error=case.error,
        scores={score.evaluator: score.value for score in case.scores},
        regressed=regressed,
    )


def report_detail(report: Any, baseline: Any | None = None) -> s.RegressionDetail:
    """One regression run, and which cases became worse (UI §26)."""
    was_passing = (
        {case.item_id for case in baseline.cases if case.passed} if baseline is not None else set()
    )
    cases = [
        _case_view(case, regressed=not case.passed and case.item_id in was_passing)
        for case in report.cases
    ]
    return s.RegressionDetail(
        **report_summary(report).model_dump(),
        metrics=[
            s.MetricSummaryView(
                name=metric.name,
                passed=metric.passed,
                total=metric.total,
                mean=metric.mean,
                pass_rate=metric.pass_rate * 100.0,
                threshold=metric.threshold,
                baseline_mean=report.baseline_metrics.get(metric.name),
            )
            for metric in report.metrics
        ],
        gates=[
            s.GateView(
                name=gate.name,
                passed=gate.passed,
                actual=gate.actual,
                limit=gate.limit,
                comparison=gate.comparison,
                detail=gate.detail,
            )
            for gate in report.gates
        ],
        cases=cases,
        regressions=[case for case in cases if case.regressed],
        baseline=report_summary(baseline) if baseline is not None else None,
    )


def experiment_plan(request: s.ExperimentRequest) -> s.ExperimentPlan:
    """The command that runs this experiment (UI §26).

    A regression executes the agent, and the agent is the user's own code --
    which the console does not have and should not import. So the form
    produces the exact command to run, and the report lands back here when it
    finishes. Saying that plainly beats a button that cannot work (UI §48).
    """
    parts = ["rewyn", "test", request.dataset]
    if request.target:
        parts += ["--target", request.target]
    for evaluator in request.evaluators:
        parts += ["--evaluator", evaluator]
    if request.baseline:
        parts += ["--baseline", request.baseline]
    if request.min_success is not None:
        parts += ["--min-success", str(request.min_success)]
    if request.max_cost is not None:
        parts += ["--max-cost", str(request.max_cost)]
    if request.concurrency > 1:
        parts += ["--concurrency", str(request.concurrency)]
    return s.ExperimentPlan(
        dataset=request.dataset,
        command=" ".join(parts),
        explanation=(
            "A regression runs your agent against every case, so it runs where your "
            "code is. This command does exactly what the form describes; the report "
            "appears here as soon as it finishes."
        ),
    )
