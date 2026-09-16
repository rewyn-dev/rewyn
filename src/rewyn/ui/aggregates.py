"""Project-level documents: overview and global search (UI spec §5, §6, §46).

The overview is not a wall of charts. UI §6 says it answers one question --
"is my AI system healthy?" -- and UI §46 says a chart earns its place only by
answering a question. So this module produces answers: a status, five numbers,
what changed recently, what looks wrong, and the latest runs.

Everything here is derived from data both surfaces already hold, and every
claim is evidence-backed: a "recent change" is a version transition that was
actually recorded, and the incidents come from
:mod:`rewyn.ui.incidents`, which names the runs each was computed from.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from rewyn.ui import incidents as incident_detection
from rewyn.ui import schemas as s

HealthStatus = Literal["healthy", "degraded", "unhealthy", "unknown"]

# Group names for global search, in the order UI §5 prints them.
SEARCH_GROUPS: tuple[str, ...] = (
    "Runs",
    "Agents",
    "Users",
    "Tools",
    "MCP servers",
    "Skills",
    "Prompts",
    "Datasets",
    "Errors",
)

_FAILURE_RATE_DEGRADED = 5.0
_FAILURE_RATE_UNHEALTHY = 20.0


def health(stats: Mapping[str, float]) -> HealthStatus:
    """A status word backed by the failure rate, not a vibe (UI §6)."""
    runs = stats.get("runs", 0.0)
    if runs <= 0:
        return "unknown"
    failure_rate = stats.get("failed", 0.0) / runs * 100.0
    if failure_rate >= _FAILURE_RATE_UNHEALTHY:
        return "unhealthy"
    if failure_rate >= _FAILURE_RATE_DEGRADED:
        return "degraded"
    return "healthy"


def metrics(stats: Mapping[str, float], *, regressions: int = 0) -> list[s.OverviewMetric]:
    """The five numbers UI §6 prints, in that order."""
    return [
        s.OverviewMetric(
            label="Success rate", value=stats.get("success_rate", 0.0), unit="percent"
        ),
        s.OverviewMetric(label="Regression", value=float(regressions), unit="count"),
        s.OverviewMetric(
            label="Avg latency", value=stats.get("avg_latency_ms", 0.0) / 1000.0, unit="seconds"
        ),
        s.OverviewMetric(label="Avg cost", value=stats.get("avg_cost", 0.0), unit="currency"),
        s.OverviewMetric(label="Runs", value=stats.get("runs", 0.0), unit="count"),
    ]


def recent_changes(
    runs: Sequence[s.RunSummary],
    versions: Mapping[str, Mapping[str, str]],
    *,
    limit: int = 10,
) -> list[s.RecentChange]:
    """Dependency versions that changed between consecutive runs of an agent (UI §6).

    ``runs`` must be newest first. A change is only reported when the same
    agent ran before and after it, so this is an observation, not a guess.
    """
    seen: dict[str, tuple[s.RunSummary, Mapping[str, str]]] = {}
    changes: list[s.RecentChange] = []
    for run in runs:
        key = run.agent or run.name
        current = versions.get(run.id, {})
        newer = seen.get(key)
        if newer is not None:
            after_run, after = newer
            for dependency, before_version in current.items():
                after_version = after.get(dependency)
                if after_version is None or after_version == before_version:
                    continue
                kind, _, name = dependency.partition(":")
                changes.append(
                    s.RecentChange(
                        at=after_run.started_at,
                        kind=kind,
                        name=name,
                        before=before_version,
                        after=after_version,
                        run_id=after_run.id,
                        summary=f"{name} {before_version} → {after_version}",
                    )
                )
        seen[key] = (run, current)
    changes.sort(key=lambda c: c.at, reverse=True)
    return changes[:limit]


def overview(
    *,
    project: str,
    environment: str | None,
    stats: Mapping[str, float],
    runs: Sequence[s.RunSummary],
    versions: Mapping[str, Mapping[str, str]],
    regressions: int = 0,
) -> s.Overview:
    """The whole overview document (UI §6)."""
    return s.Overview(
        project=project,
        environment=environment,
        status=health(stats),
        metrics=metrics(stats, regressions=regressions),
        recent_changes=recent_changes(runs, versions),
        incidents=incident_detection.detect(runs),
        recent_runs=list(runs[:10]),
    )


def search(
    query: str,
    *,
    runs: Sequence[s.RunSummary],
    agents: Sequence[tuple[str, str]] = (),
    tools: Sequence[tuple[str, str]] = (),
    mcp: Sequence[tuple[str, str]] = (),
    skills: Sequence[tuple[str, str]] = (),
    prompts: Sequence[tuple[str, str]] = (),
    datasets: Sequence[tuple[str, str]] = (),
    limit: int = 8,
) -> s.SearchResults:
    """Grouped results in the order UI §5 prints them."""
    needle = query.strip().lower()
    hits: list[s.SearchHit] = []
    if not needle:
        return s.SearchResults(query=query, groups=[], hits=[], total=0)

    def add(group: str, pairs: Sequence[tuple[str, str]], href: str) -> None:
        matched = [p for p in pairs if needle in p[0].lower()][:limit]
        hits.extend(
            s.SearchHit(
                group=group,
                id=name,
                label=name,
                detail=f"v{version}" if version and version != "unversioned" else "",
                href=href.format(name=name),
            )
            for name, version in matched
        )

    matched_runs = [
        r
        for r in runs
        if needle in r.id.lower()
        or needle in r.name.lower()
        or (r.agent and needle in r.agent.lower())
    ][:limit]
    hits.extend(
        s.SearchHit(
            group="Runs",
            id=r.id,
            label=r.id,
            detail=f"{r.agent or r.name} · {r.status}",
            href=f"/runs/{r.id}",
        )
        for r in matched_runs
    )
    add("Agents", agents, "/agents/{name}")
    users = sorted({r.user for r in runs if r.user and needle in r.user.lower()})[:limit]
    hits.extend(
        s.SearchHit(group="Users", id=u, label=u, detail="", href=f"/runs?user={u}") for u in users
    )
    add("Tools", tools, "/tools/{name}")
    add("MCP servers", mcp, "/mcp/{name}")
    add("Skills", skills, "/skills/{name}")
    add("Prompts", prompts, "/prompts/{name}")
    add("Datasets", datasets, "/datasets/{name}")
    errors = sorted(
        {r.error.split("\n")[0][:120] for r in runs if r.error and needle in r.error.lower()}
    )
    hits.extend(
        s.SearchHit(group="Errors", id=e, label=e, detail="", href=f"/runs?q={e[:40]}")
        for e in errors[:limit]
    )
    groups = [g for g in SEARCH_GROUPS if any(h.group == g for h in hits)]
    hits.sort(key=lambda h: SEARCH_GROUPS.index(h.group) if h.group in SEARCH_GROUPS else 99)
    return s.SearchResults(query=query, groups=groups, hits=hits, total=len(hits))
