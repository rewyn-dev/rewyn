"""The AI development workspace, and the loop it closes (UI spec §37, §59, §60).

UI §37 draws one column -- config → run → inspect (context, memory, MCP,
skills, tools) → replay → evaluate → regression -- and asks the console to
feel like an IDE rather than a set of unrelated pages. UI §60 draws the wider
loop that column sits inside: build → debug → replay → experiment → evaluate
→ regression test → release → monitor → learn → build.

This module builds both as documents, because the useful part of a lifecycle
picture is not the picture. It is knowing which step you are actually on. So
every stage carries its own state, read from what this agent has recorded: a
count, a status, and the one sentence that says whether the step is done and
where to go next. A stage that has never happened says what would start it,
which is what makes the loop navigable rather than decorative (UI §47).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from rewyn.ui import schemas as s

# UI §37's column, and the north-star question each band answers (UI §59).
STAGES: tuple[tuple[str, str, str], ...] = (
    ("config", "Agent configuration", "What is my AI made of?"),
    ("run", "Run", "What is my AI doing?"),
    ("context", "Context", "What information did it use?"),
    ("memory", "Memory", "What does it remember?"),
    ("mcp", "MCP", "Which servers did it depend on?"),
    ("skills", "Skills", "Which skills did it load?"),
    ("tools", "Tools", "What did it actually do?"),
    ("replay", "Replay", "Can I reproduce it?"),
    ("evaluate", "Evaluation", "Is it good?"),
    ("regression", "Regression", "Did my change make it worse?"),
)

# UI §60's loop, in order, ending where it begins.
LOOP: tuple[tuple[str, str, str], ...] = (
    ("build", "Build", "What is my AI made of?"),
    ("debug", "Debug", "What is my AI doing?"),
    ("replay", "Replay", "Can I reproduce it?"),
    ("experiment", "Experiment", "Which variant wins?"),
    ("evaluate", "Evaluate", "Is it good?"),
    ("regression", "Regression test", "Did my change make it worse?"),
    ("release", "Release", "Is it safe to deploy?"),
    ("monitor", "Monitor", "Is my AI system healthy?"),
    ("learn", "Learn", "What could have changed outside my code?"),
)

# The panel each inspect band reads its count from (UI §11, §16-§19).
PANEL_FIELDS: dict[str, str] = {
    "context": "context",
    "memory": "memory",
    "mcp": "mcp",
    "skills": "skills",
    "tools": "tools",
}

COMPONENT_HREFS: dict[str, str] = {
    "model": "/models/{name}",
    "embedding_model": "/models/{name}",
    "prompt": "/prompts/{name}",
    "skill": "/skills/{name}",
    "tool": "/tools/{name}",
    "mcp_server": "/mcp/{name}",
    "context": "/context/{name}",
    "memory": "/memory/{name}",
    "graph": "/graphs/{name}",
    "dataset": "/datasets/{name}",
}


def _stage(
    key: str,
    label: str,
    question: str,
    *,
    href: str,
    summary: str = "",
    detail: str = "",
    count: int | None = None,
    status: str = "idle",
) -> s.WorkspaceStage:
    return s.WorkspaceStage(
        key=key,
        label=label,
        question=question,
        ready=summary != "",
        href=href,
        summary=summary,
        detail=detail,
        count=count,
        status=status,
    )


def workspace(
    agent: s.AgentDetail,
    *,
    latest: s.RunSummary | None = None,
    counts: s.PanelCounts | None = None,
    replay: s.ReplayPlan | None = None,
    reports: Sequence[s.RegressionSummary] = (),
    incidents: Sequence[s.Incident] = (),
    release: s.ReleaseView | None = None,
    environment: str | None = None,
) -> s.WorkspaceView:
    """One agent's whole development frame (UI §37)."""
    run_href = f"/runs/{latest.id}" if latest else f"/runs?agent={quote(agent.name)}"
    report = reports[0] if reports else None
    components = [
        s.WorkspaceComponent(
            kind=dependency.kind,
            name=dependency.name,
            version=dependency.version,
            href=(
                COMPONENT_HREFS[dependency.kind].format(name=quote(dependency.name))
                if dependency.kind in COMPONENT_HREFS
                else None
            ),
        )
        for dependency in agent.dependencies
    ]

    stages: list[s.WorkspaceStage] = [
        _stage(
            "config",
            "Agent configuration",
            "What is my AI made of?",
            href=f"/agents/{quote(agent.name)}",
            summary=(
                f"v{agent.version} · {len(components)} components"
                if components or agent.version != "unversioned"
                else ""
            ),
            detail=(
                f"{agent.versions} version{'s' if agent.versions != 1 else ''} recorded"
                if agent.versions
                else ""
            ),
            count=len(components) or None,
            status="ok" if components else "idle",
        ),
        _stage(
            "run",
            "Run",
            "What is my AI doing?",
            href=run_href,
            summary=(
                f"{agent.runs} runs · {agent.success_rate:.0f}% succeeding" if agent.runs else ""
            ),
            detail=(f"latest {latest.id} {latest.status}" if latest else "No run recorded yet."),
            count=agent.runs or None,
            status=(
                "idle"
                if not agent.runs
                else "ok"
                if agent.success_rate >= 95.0
                else "warn"
                if agent.success_rate >= 80.0
                else "fail"
            ),
        ),
    ]

    for key, label, question in STAGES[2:7]:
        field = PANEL_FIELDS[key]
        total = int(getattr(counts, field, 0)) if counts is not None else 0
        stages.append(
            _stage(
                key,
                label,
                question,
                href=f"{run_href}?tab={key}" if latest else run_href,
                summary=f"{total} recorded in the latest run" if total else "",
                detail=("" if total else f"The latest run used no {label.lower()}."),
                count=total or None,
                status="ok" if total else "idle",
            )
        )

    replayable = [c for c in (replay.components if replay else []) if c.supported]
    stages.append(
        _stage(
            "replay",
            "Replay",
            "Can I reproduce it?",
            href=f"/runs/{latest.id}/replay" if latest else "/replay",
            summary=(
                f"{replay.mode} replay ready · {len(replayable)} components can be swapped"
                if replay is not None
                else ""
            ),
            detail="" if replay is not None else "Record a run, then replay it.",
            count=len(replayable) or None,
            status="ok" if replay is not None else "idle",
        )
    )
    stages.append(
        _stage(
            "evaluate",
            "Evaluation",
            "Is it good?",
            href="/evaluations",
            summary=(f"score {agent.eval_score:.2f}" if agent.eval_score is not None else ""),
            detail=(
                ""
                if agent.eval_score is not None
                else "No run of this agent carries an evaluator score."
            ),
            status="ok" if agent.eval_score is not None else "idle",
        )
    )
    stages.append(
        _stage(
            "regression",
            "Regression",
            "Did my change make it worse?",
            href=f"/regression/{report.id}" if report else "/regression",
            summary=(
                f"{report.succeeded}/{report.tests} passing on {report.dataset}" if report else ""
            ),
            detail=(
                "" if report else "Save a run as a test, then run the dataset to get a baseline."
            ),
            count=report.tests if report else None,
            status=("ok" if report.passed else "fail") if report else "idle",
        )
    )

    return s.WorkspaceView(
        agent=agent.name,
        version=agent.version,
        environment=environment,
        components=components,
        stages=stages,
        loop=loop(
            agent,
            latest=latest,
            reports=reports,
            incidents=incidents,
            release=release,
        ),
        latest_run=latest,
        recent_runs=list(agent.recent_runs[:8]),
        incidents=list(incidents),
        next_step=next_step(stages),
    )


def next_step(stages: Sequence[s.WorkspaceStage]) -> str:
    """The first band that is not done, phrased as the thing to do next (UI §47)."""
    broken = next((s for s in stages if s.status == "fail"), None)
    if broken is not None:
        return f"{broken.label}: {broken.summary or broken.detail}"
    pending = next((s for s in stages if not s.ready), None)
    if pending is None:
        return "Every stage has evidence behind it. Release when the gate agrees."
    return f"{pending.label}: {pending.detail or pending.question}"


def loop(
    agent: s.AgentSummary,
    *,
    latest: s.RunSummary | None = None,
    reports: Sequence[s.RegressionSummary] = (),
    incidents: Sequence[s.Incident] = (),
    release: s.ReleaseView | None = None,
    experiments: int = 0,
    drift_findings: int = 0,
) -> list[s.WorkspaceStage]:
    """UI §60's loop, with this agent's position in it."""
    name = quote(agent.name)
    report = reports[0] if reports else None
    failing = [r for r in (latest.error,) if r] if latest else []
    open_incidents = [i for i in incidents if i.affected_runs]

    return [
        _stage(
            "build",
            "Build",
            "What is my AI made of?",
            href=f"/agents/{name}",
            summary=f"v{agent.version}",
            detail=f"{agent.versions} versions recorded",
            status="ok",
        ),
        _stage(
            "debug",
            "Debug",
            "What is my AI doing?",
            href=f"/runs/{latest.id}" if latest else f"/runs?agent={name}",
            summary=f"{agent.runs} runs" if agent.runs else "",
            detail="No run recorded yet." if not agent.runs else "",
            count=agent.runs or None,
            status="fail" if failing else "ok" if agent.runs else "idle",
        ),
        _stage(
            "replay",
            "Replay",
            "Can I reproduce it?",
            href=f"/runs/{latest.id}/replay" if latest else "/replay",
            summary="ready" if latest else "",
            detail="Replay starts from a recorded run." if not latest else "",
            status="ok" if latest else "idle",
        ),
        _stage(
            "experiment",
            "Experiment",
            "Which variant wins?",
            href="/experiments",
            summary=f"{experiments} comparisons" if experiments else "",
            detail="Run one dataset against two targets to compare them."
            if not experiments
            else "",
            count=experiments or None,
            status="ok" if experiments else "idle",
        ),
        _stage(
            "evaluate",
            "Evaluate",
            "Is it good?",
            href="/evaluations",
            summary=f"score {agent.eval_score:.2f}" if agent.eval_score is not None else "",
            detail="No evaluator has scored this agent." if agent.eval_score is None else "",
            status="ok" if agent.eval_score is not None else "idle",
        ),
        _stage(
            "regression",
            "Regression test",
            "Did my change make it worse?",
            href=f"/regression/{report.id}" if report else "/regression",
            summary=f"{report.succeeded}/{report.tests} passing" if report else "",
            detail="No regression report for this agent." if not report else "",
            count=report.tests if report else None,
            status=("ok" if report.passed else "fail") if report else "idle",
        ),
        _stage(
            "release",
            "Release",
            "Is it safe to deploy?",
            href=f"/releases?agent={name}",
            summary=release.status.upper() if release is not None else "",
            detail=(
                "; ".join(release.blocked_by)
                if release is not None and release.blocked_by
                else ("No release candidate yet." if release is None else "")
            ),
            status=(
                "ok"
                if release is not None and release.status == "ready"
                else "fail"
                if release is not None and release.status == "blocked"
                else "warn"
                if release is not None
                else "idle"
            ),
        ),
        _stage(
            "monitor",
            "Monitor",
            "Is my AI system healthy?",
            href="/incidents" if open_incidents else "/",
            summary=(
                f"{len(open_incidents)} incident{'s' if len(open_incidents) != 1 else ''}"
                if open_incidents
                else "no incidents"
            ),
            detail=open_incidents[0].summary if open_incidents else "",
            count=len(open_incidents) or None,
            status="fail" if open_incidents else "ok",
        ),
        _stage(
            "learn",
            "Learn",
            "What could have changed outside my code?",
            href=f"/drift?agent={name}",
            summary=(
                f"{drift_findings} dependencies moved"
                if drift_findings
                else "nothing moved underneath"
            ),
            detail="Feeds the next change back into Build.",
            count=drift_findings or None,
            status="warn" if drift_findings else "ok",
        ),
    ]


def component_sections(components: Sequence[s.WorkspaceComponent]) -> dict[str, list[Any]]:
    """Group an agent's components the way §37 stacks them."""
    grouped: dict[str, list[Any]] = {}
    for component in components:
        grouped.setdefault(component.kind, []).append(component)
    return grouped
