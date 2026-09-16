"""``rewyn ui``: the local console (UI spec §44).

A developer who has run ``pip install rewyn`` must be able to inspect their
own executions with no account, no key and no cloud. This server does that:
it binds to loopback, reads the developer's ``.rewyn/``, and serves the
same ``/v1`` contract the cloud serves, so the same frontend works against
either (UI §44, §52).

It is deliberately read-only in this phase. Replay and the workspaces that
mutate state arrive with P1, and they arrive on both surfaces at once.
"""

from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from rewyn import __version__
from rewyn.core.event import EventType
from rewyn.core.types import utcnow
from rewyn.replay.recorder import RecordedRun
from rewyn.storage.local import LocalStore, RunNotFoundError
from rewyn.ui import (
    aggregates,
    collaboration,
    demo,
    explain,
    incidents,
    intelligence,
    lifecycle,
    live,
    onboarding,
    projections,
    registries,
    workspaces,
)
from rewyn.ui import schemas as s
from rewyn.ui.index import RunIndex, RunQuery
from rewyn.ui.workspaces import ReplayJobs

PREFIX = s.CONSOLE_PREFIX


def _approval_view(item: Any) -> s.PendingApprovalView:
    """One inbox entry as the console shows it (UI §35)."""
    decision = item.decision
    return s.PendingApprovalView(
        request_id=item.request.id,
        action=item.request.action,
        risk=item.request.risk,
        details=dict(item.request.details),
        requested_at=item.request.requested_at,
        run_id=item.request.run_id,
        decision=(
            "pending" if decision is None else ("approved" if decision.approved else "rejected")
        ),
        by=decision.by if decision else None,
        reason=decision.reason if decision else None,
        expires_at=item.expires_at,
        expired=item.expired(),
    )


def _bad_subject(exc: Exception) -> HTTPException:
    """A comment on something the console cannot open is a mistake worth naming (UI §48)."""
    return HTTPException(
        status_code=422,
        detail=s.ProblemDetail(
            error="That is not something to comment on",
            detail=str(exc),
            action="Browse runs",
            href="/runs",
        ).model_dump(),
    )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


STATIC_DIR = Path(__file__).parent / "static"
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"})

FEATURES = [
    "runs",
    "replay",
    "compare",
    "diff",
    "datasets",
    "save-as-test",
    "agents",
    "registries",
    "evaluations",
    "regression",
    "live",
    "streaming",
    "approvals",
    "dependencies",
    "drift",
    "cost",
    "releases",
    "experiments",
    "notifications",
    "run-detail",
    "timeline",
    "graph",
    "context",
    "model",
    "prompt",
    "memory",
    "tools",
    "mcp",
    "skills",
    "sessions",
    "environments",
    "search",
    "overview",
    "export",
    "incidents",
    "comments",
    "saved-views",
    "workspace",
    "explain",
    "onboarding",
    "demo",
]


class ConsoleState:
    """Everything a request needs: the store, and an index kept warm."""

    def __init__(self, store: LocalStore, *, refresh_interval: float = 1.0) -> None:
        self.store = store
        self.index = RunIndex(store)
        self.replays = ReplayJobs()
        self.collab = collaboration.CollabStore(store)
        self.refresh_interval = refresh_interval
        self._last_refresh = 0.0
        self._lock = threading.Lock()

    def refresh(self, *, force: bool = False) -> None:
        """Re-index changed runs, at most once per interval (UI §50).

        Routes run in a thread pool and a screen opens several at once, so
        this is locked: without it one request can query the index while
        another is rebuilding it and see a half-written table.
        """
        with self._lock:
            now = time.monotonic()
            if not force and (now - self._last_refresh) < self.refresh_interval:
                return
            self._last_refresh = now
            self.index.refresh()

    def recorded(self, run_id: str) -> RecordedRun:
        resolved = self.store.resolve_run_id(run_id)
        return RecordedRun(self.store.read_manifest(resolved), self.store.read_events(resolved))

    def manifest_only(self, run_id: str) -> RecordedRun:
        """A run without its events -- enough for dependency comparisons."""
        return RecordedRun(self.store.read_manifest(run_id), [])

    def recorded_dependencies(self, run_id: str) -> list[Any]:
        """A run's dependency list without reading its event log."""
        return list(self.store.read_manifest(run_id).dependencies)

    def previous_run(self, summary: s.RunSummary) -> RecordedRun | None:
        """The run before this one for the same agent (UI §18 'changed since')."""
        page = self.index.search(RunQuery(agent=summary.agent, until=summary.started_at, limit=2))
        for candidate in page.runs:
            if candidate.id != summary.id:
                return self.manifest_only(candidate.id)
        return None


def console_state(request: Request) -> ConsoleState:
    state: ConsoleState = request.app.state.console
    return state


def recorded_run(
    state: Annotated[ConsoleState, Depends(console_state)], run_id: str
) -> RecordedRun:
    """Load the run named in the path. Raises ``RunNotFoundError`` if absent."""
    return state.recorded(run_id)


Recorded = Annotated[RecordedRun, Depends(recorded_run)]


def build_app(store: LocalStore | None = None, *, static_dir: Path | None = None) -> FastAPI:
    """Build the local console application."""
    state = ConsoleState(store or LocalStore())
    assets = static_dir if static_dir is not None else STATIC_DIR

    app = FastAPI(
        title="Rewyn console",
        version=__version__,
        description="The local Rewyn console (UI spec §44).",
        docs_url=f"{PREFIX}/docs",
        openapi_url=f"{PREFIX}/openapi.json",
    )

    app.state.console = state

    @app.middleware("http")
    async def loopback_only(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """The local console serves one machine: this one."""
        host = (request.headers.get("host") or "").split(":")[0]
        if host and host not in LOOPBACK:
            return JSONResponse(
                status_code=403,
                content=s.ProblemDetail(
                    error="Not reachable from this host",
                    detail=(
                        "The local console only answers requests from this machine. "
                        "Use the cloud console to share runs with a team."
                    ),
                ).model_dump(),
            )
        return await call_next(request)

    @app.exception_handler(RunNotFoundError)
    async def missing_run(request: Request, exc: Exception) -> Response:
        run_id = exc.run_id if isinstance(exc, RunNotFoundError) else "?"
        return JSONResponse(
            status_code=404,
            content=s.ProblemDetail(
                error="Run not found",
                detail=(
                    f"No run matching {run_id!r} in {state.store.home}. "
                    "It may have been deleted, or recorded under a different REWYN_HOME."
                ),
                action="Browse runs",
                href="/runs",
            ).model_dump(),
        )

    # Meta ---------------------------------------------------------------------
    @app.get(f"{PREFIX}/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "surface": "local", "api_version": s.CONSOLE_API_VERSION}

    def _dataset_names() -> list[Any]:
        from rewyn.evaluation.dataset import list_datasets as load_datasets

        return list(load_datasets(home=state.store.home))

    def _counts() -> s.ConsoleCounts:
        """How much of the product is reachable in this project yet (UI §47)."""
        stats = state.index.stats()
        kinds = registries.REGISTRY_KINDS["agents"]
        versions: dict[str, set[str]] = {}
        for name, version, _runs, _first, _last in state.index.registry(kinds):
            versions.setdefault(name, set()).add(version)
        return s.ConsoleCounts(
            runs=int(stats.get("runs", 0)),
            agents=len(versions),
            datasets=len(_dataset_names()),
            reports=len(_reports()),
            failures=int(stats.get("failed", 0)),
            versions=max((len(v) for v in versions.values()), default=0),
        )

    def _location() -> s.ProjectLocation:
        from rewyn.core.settings import get_settings

        return onboarding.location(
            surface="local",
            project=get_settings().project,
            home=str(state.store.home.resolve()),
        )

    @app.get(f"{PREFIX}/capabilities")
    def capabilities() -> s.Capabilities:
        """What this surface serves, so the UI hides what it cannot (UI §45)."""
        from rewyn.core.settings import get_settings

        state.refresh()
        return s.Capabilities(
            surface="local",
            sdk_version=__version__,
            project=get_settings().project,
            role="owner",
            features=FEATURES,
            environments=[e.name for e in state.index.environments()],
            location=_location(),
            counts=_counts(),
            demo_loaded=demo.is_loaded(state.store),
        )

    # First run (UI §47) --------------------------------------------------------
    @app.get(f"{PREFIX}/onboarding")
    def first_run() -> s.Onboarding:
        """What this is and what to do next, for a project with nothing in it."""
        state.refresh()
        return onboarding.document(
            place=_location(),
            counts=_counts(),
            demo_loaded=demo.is_loaded(state.store),
            can_load_demo=True,
        )

    @app.post(f"{PREFIX}/demo", status_code=201)
    async def load_demo() -> s.Onboarding:
        """Seed a small demo project, so the console can be read before it is useful.

        Every run it writes is tagged ``demo`` and ``DELETE`` removes exactly
        those: exploring the product must not leave anything behind that could
        later be mistaken for your own data.
        """
        await demo.aload(state.store)
        state.refresh(force=True)
        return onboarding.document(
            place=_location(), counts=_counts(), demo_loaded=True, can_load_demo=True
        )

    @app.delete(f"{PREFIX}/demo")
    def clear_demo() -> s.Onboarding:
        """Delete the demo runs, and only those."""
        demo.clear(state.store)
        state.refresh(force=True)
        return onboarding.document(
            place=_location(), counts=_counts(), demo_loaded=False, can_load_demo=True
        )

    # Overview -----------------------------------------------------------------
    @app.get(f"{PREFIX}/overview")
    def overview(environment: str | None = None) -> s.Overview:
        """ "Is my AI system healthy?" (UI §6)."""
        from rewyn.core.settings import get_settings

        state.refresh()
        stats = state.index.stats(environment=environment)
        page = state.index.search(RunQuery(environment=environment, limit=200))
        versions = state.index.dependency_versions([r.id for r in page.runs])
        return aggregates.overview(
            project=get_settings().project,
            environment=environment,
            stats=stats,
            runs=page.runs,
            versions=versions,
        )

    # Runs ---------------------------------------------------------------------
    @app.get(f"{PREFIX}/runs")
    def list_runs(
        *,
        q: str | None = None,
        agent: str | None = None,
        model: str | None = None,
        status: str | None = None,
        user: str | None = None,
        environment: str | None = None,
        session_id: str | None = None,
        tool: str | None = None,
        mcp: str | None = None,
        skill: str | None = None,
        tag: str | None = None,
        error: bool | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        min_cost: float | None = None,
        max_cost: float | None = None,
        min_latency_ms: float | None = None,
        max_latency_ms: float | None = None,
        min_score: float | None = None,
        max_score: float | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        cursor: str | None = None,
    ) -> s.RunPage:
        """The runs table with every filter UI §7 lists."""
        state.refresh()
        return state.index.search(
            RunQuery(
                q=q,
                agent=agent,
                model=model,
                status=status,
                user=user,
                environment=environment,
                session_id=session_id,
                tool=tool,
                mcp=mcp,
                skill=skill,
                tag=tag,
                error=error,
                since=since,
                until=until,
                min_cost=min_cost,
                max_cost=max_cost,
                min_latency_ms=min_latency_ms,
                max_latency_ms=max_latency_ms,
                min_score=min_score,
                max_score=max_score,
                limit=limit,
                cursor=cursor,
            )
        )

    @app.get(f"{PREFIX}/runs/facets")
    def run_facets() -> s.RunFacets:
        """The filter vocabulary, with counts (UI §7)."""
        state.refresh()
        return state.index.facets()

    @app.get(f"{PREFIX}/runs/{{run_id}}")
    def run_detail(recorded: Recorded) -> s.RunDetail:
        detail = projections.run_detail(recorded)
        # A verdict on this run was recorded by the evaluation that produced
        # it, which is a different run; the index is where they are joined.
        state.refresh()
        indexed = state.index.get(recorded.id)
        if indexed is not None and indexed.eval_score is not None:
            detail = detail.model_copy(
                update={"run": detail.run.model_copy(update={"eval_score": indexed.eval_score})}
            )
        return detail

    @app.get(f"{PREFIX}/runs/{{run_id}}/timeline")
    def run_timeline(
        recorded: Recorded,
        after_seq: int = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> s.TimelineView:
        return projections.timeline(recorded, after_seq=after_seq, limit=limit)

    @app.get(f"{PREFIX}/runs/{{run_id}}/events")
    def run_events(
        recorded: Recorded,
        after_seq: int = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> s.EventPage:
        return projections.events_page(recorded, after_seq=after_seq, limit=limit)

    @app.get(f"{PREFIX}/runs/{{run_id}}/graph")
    def run_graph(recorded: Recorded) -> s.GraphView:
        return projections.graph(recorded)

    @app.get(f"{PREFIX}/runs/{{run_id}}/context")
    def run_context(recorded: Recorded) -> s.ContextView:
        return projections.context(recorded, role="owner")

    @app.get(f"{PREFIX}/runs/{{run_id}}/model")
    def run_model(recorded: Recorded) -> s.ModelView:
        return projections.model(recorded)

    @app.get(f"{PREFIX}/runs/{{run_id}}/prompt")
    def run_prompt(recorded: Recorded) -> s.PromptView:
        return projections.prompt(recorded)

    @app.get(f"{PREFIX}/runs/{{run_id}}/memory")
    def run_memory(recorded: Recorded) -> s.MemoryView:
        return projections.memory(recorded)

    @app.get(f"{PREFIX}/runs/{{run_id}}/tools")
    def run_tools(recorded: Recorded) -> s.ToolView:
        return projections.tools(recorded)

    @app.get(f"{PREFIX}/runs/{{run_id}}/mcp")
    def run_mcp(recorded: Recorded) -> s.McpView:
        summary = projections.run_summary(recorded.manifest)
        return projections.mcp(recorded, previous=state.previous_run(summary))

    @app.get(f"{PREFIX}/runs/{{run_id}}/skills")
    def run_skills(recorded: Recorded) -> s.SkillsView:
        return projections.skills(recorded)

    @app.get(f"{PREFIX}/runs/{{run_id}}/guardrails")
    def run_guardrails(recorded: Recorded) -> list[s.GuardrailView]:
        return projections.guardrails(recorded)

    @app.get(f"{PREFIX}/runs/{{run_id}}/approvals")
    def run_approvals(recorded: Recorded) -> list[s.ApprovalView]:
        return projections.approvals(recorded)

    @app.get(f"{PREFIX}/runs/{{run_id}}/export")
    def run_export(run_id: str) -> FileResponse:
        """The Export action on the run header (UI §8), as a portable bundle."""
        from rewyn.replay.export import export_run

        resolved = state.store.resolve_run_id(run_id)
        destination = Path(tempfile.mkdtemp(prefix="rewyn-export-"))
        bundle = export_run(resolved, destination, store=state.store, archive=True)
        return FileResponse(bundle, media_type="application/zip", filename=f"{resolved}.zip")

    # Navigation ---------------------------------------------------------------
    @app.get(f"{PREFIX}/sessions")
    def sessions(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[s.SessionView]:
        state.refresh()
        return state.index.sessions(limit=limit)

    @app.get(f"{PREFIX}/environments")
    def environments() -> list[s.EnvironmentView]:
        state.refresh()
        return state.index.environments()

    @app.get(f"{PREFIX}/search")
    def search(q: str) -> s.SearchResults:
        """Global search (UI §5)."""
        state.refresh()
        page = state.index.search(RunQuery(q=q, limit=200))
        return aggregates.search(
            q,
            runs=page.runs,
            agents=_names(state.index, ("agent", "graph")),
            tools=_names(state.index, ("tool",)),
            mcp=_names(state.index, ("mcp_server", "mcp")),
            skills=_names(state.index, ("skill",)),
            prompts=_names(state.index, ("prompt",)),
            datasets=_names(state.index, ("dataset",)),
        )

    # Replay, compare and datasets (UI §20-§25) --------------------------------
    @app.post(f"{PREFIX}/runs/{{run_id}}/replay:plan")
    def plan_replay(recorded: Recorded, body: s.ReplayRequest) -> s.ReplayPlan:
        """What a replay would do, before running it (UI §21)."""
        return workspaces.plan_replay(recorded, body)

    @app.post(f"{PREFIX}/runs/{{run_id}}/replay", status_code=202)
    async def start_replay(recorded: Recorded, body: s.ReplayRequest) -> s.ReplayView:
        """Start a replay. Poll ``/replays/{id}`` for the result (UI §20)."""
        from rewyn.replay.replay import areplay

        plan = workspaces.plan_replay(recorded, body)
        unsupported = [c.component for c in plan.components if not c.supported]
        if unsupported:
            raise HTTPException(
                status_code=422,
                detail=s.ProblemDetail(
                    error="This replay cannot run here",
                    detail=(
                        f"{', '.join(unsupported)} cannot be changed from the console. "
                        "Every other control can, and the run can always be reproduced "
                        "exactly from its recording."
                    ),
                    action="Replay with recorded responses",
                    href=f"/runs/{recorded.id}?tab=replay",
                ).model_dump(),
            )
        job = state.replays.start(
            plan, areplay(recorded, store=state.store, **workspaces.replay_arguments(plan))
        )
        return workspaces.replay_view(job)

    @app.get(f"{PREFIX}/replays/{{job_id}}")
    def get_replay(job_id: str) -> s.ReplayView:
        job = state.replays.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail=s.ProblemDetail(
                    error="Replay not found",
                    detail=(
                        "Replays live for the life of the console process, so this one "
                        "is gone. Start it again from the run."
                    ),
                    action="Browse runs",
                    href="/runs",
                ).model_dump(),
            )
        return workspaces.replay_view(job)

    @app.get(f"{PREFIX}/replays")
    def list_replays() -> list[s.ReplayView]:
        return [workspaces.replay_view(job) for job in state.replays.all()]

    @app.get(f"{PREFIX}/runs/{{run_id}}/diff")
    def diff_runs(recorded: Recorded, against: str) -> s.DiffView:
        """Compare two runs: observed differences, then hypotheses (UI §22, §23)."""
        from rewyn.replay.diff import diff as diff_runs_impl

        other = state.recorded(against)
        result = diff_runs_impl(other, recorded)
        return workspaces.diff_view(result, other, recorded)

    @app.get(f"{PREFIX}/runs/{{run_id}}/comparable")
    def comparable(recorded: Recorded) -> list[s.RunSummary]:
        """Runs worth comparing this one against, best candidate first (UI §58)."""
        state.refresh()
        summary = projections.run_summary(recorded.manifest)
        page = state.index.search(RunQuery(agent=summary.agent, until=summary.started_at, limit=25))
        # A replay of this run is not what "compare with the previous run"
        # means: the replay workspace already puts those two side by side.
        candidates = [r for r in page.runs if r.id != summary.id and "replay" not in r.tags]
        succeeded = [r for r in candidates if r.status == "succeeded"]
        return succeeded + [r for r in candidates if r.status != "succeeded"]

    @app.post(f"{PREFIX}/runs/{{run_id}}/save-as-test", status_code=201)
    def save_as_test(recorded: Recorded, body: s.SaveAsTestRequest) -> s.DatasetDetail:
        """Turn a run into a permanent regression case (UI §24)."""
        from rewyn.evaluation.dataset import Dataset

        dataset = Dataset.load_or_create(body.dataset, home=state.store.home)
        metadata: dict[str, Any] = {}
        if body.evaluator:
            metadata["evaluator"] = body.evaluator
        if body.severity:
            metadata["severity"] = body.severity
        dataset.add_run(
            recorded,
            expected=body.expected,
            tags=body.tags,
            **metadata,
        )
        dataset.save(home=state.store.home)
        return workspaces.dataset_detail(dataset)

    @app.get(f"{PREFIX}/datasets")
    def list_datasets() -> list[s.DatasetSummary]:
        from rewyn.evaluation.dataset import list_datasets as load_datasets

        return [workspaces.dataset_summary(d) for d in load_datasets(home=state.store.home)]

    @app.get(f"{PREFIX}/datasets/{{name}}")
    def get_dataset(name: str) -> s.DatasetDetail:
        from rewyn.evaluation.dataset import Dataset, DatasetError

        try:
            dataset = Dataset.load(name, home=state.store.home)
        except DatasetError as exc:
            raise HTTPException(
                status_code=404,
                detail=s.ProblemDetail(
                    error="Dataset not found",
                    detail=str(exc),
                    action="Create one from a run",
                    href="/runs",
                ).model_dump(),
            ) from exc
        return workspaces.dataset_detail(dataset)

    # BUILD registries and agents (UI §29, §30) --------------------------------
    def _versions(kinds: tuple[str, ...], name: str) -> list[s.RegistryVersion]:
        state.refresh()
        return [
            s.RegistryVersion(
                version=version,
                runs=runs,
                first_seen=_parse_time(first),
                last_seen=_parse_time(last),
            )
            for entry_name, version, runs, first, last in state.index.registry(kinds)
            if entry_name == name
        ]

    @app.get(f"{PREFIX}/agents")
    def list_agents() -> list[s.AgentSummary]:
        """Every agent and graph the project has run (UI §29)."""
        state.refresh()
        kinds = registries.REGISTRY_KINDS["agents"]
        grouped: dict[str, list[s.RegistryVersion]] = {}
        for name, version, runs, first, last in state.index.registry(kinds):
            grouped.setdefault(name, []).append(
                s.RegistryVersion(
                    version=version,
                    runs=runs,
                    first_seen=_parse_time(first),
                    last_seen=_parse_time(last),
                )
            )
        summaries = [
            registries.agent_summary(
                name,
                kind="agent",
                versions=versions,
                runs=state.index.runs_using(kinds, name),
                dependencies=state.index.dependencies_of(kinds, name),
            )
            for name, versions in grouped.items()
        ]
        return sorted(summaries, key=lambda a: a.last_run_at or a.name, reverse=True)

    @app.get(f"{PREFIX}/agents/{{name}}")
    def get_agent(name: str) -> s.AgentDetail:
        state.refresh()
        kinds = registries.REGISTRY_KINDS["agents"]
        versions = _versions(kinds, name)
        if not versions:
            raise HTTPException(
                status_code=404,
                detail=s.ProblemDetail(
                    error="Agent not found",
                    detail=(
                        f"No run in this project used an agent or graph called {name!r}. "
                        "Agents appear here once they have run."
                    ),
                    action="Browse runs",
                    href="/runs",
                ).model_dump(),
            )
        runs = state.index.runs_using(kinds, name)
        dependencies = state.index.dependencies_of(kinds, name)
        summary = registries.agent_summary(
            name, kind="agent", versions=versions, runs=runs, dependencies=dependencies
        )
        return s.AgentDetail(
            **summary.model_dump(),
            version_history=sorted(versions, key=lambda v: v.last_seen or v.version, reverse=True),
            dependencies=dependencies,
            recent_runs=runs[:25],
            reports=[
                registries.report_summary(report)
                for report in _reports()
                if name in report.target or name in report.dataset
            ][:10],
        )

    @app.get(f"{PREFIX}/agents/{{name}}/versions/{{before}}..{{after}}")
    def compare_versions(name: str, before: str, after: str) -> s.ManifestDiff:
        """What changed between two versions of an agent (UI §30)."""
        from rewyn.core.manifest import BehaviorManifest

        state.refresh()
        kinds = registries.REGISTRY_KINDS["agents"]
        runs = state.index.runs_using(kinds, name, limit=500)
        selected: dict[str, list[str]] = {before: [], after: []}
        for run in runs:
            for dependency in state.recorded_dependencies(run.id):
                matches = dependency.kind in kinds and dependency.name == name
                if matches and dependency.version in selected:
                    selected[dependency.version].append(run.id)
        missing = [version for version, ids in selected.items() if not ids]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=s.ProblemDetail(
                    error="Version not found",
                    detail=f"No recorded run used {name} v{', v'.join(missing)}.",
                    action="Open the agent",
                    href=f"/agents/{name}",
                ).model_dump(),
            )
        manifests = {
            version: BehaviorManifest.from_runs(name, ids[:25], version=version, store=state.store)
            for version, ids in selected.items()
        }
        return registries.manifest_diff(manifests[before], manifests[after])

    @app.get(f"{PREFIX}/registry/{{page}}")
    def registry(page: str) -> list[s.RegistryEntry]:
        """A BUILD page: models, prompts, skills, tools, MCP, context, memory."""
        kinds = registries.REGISTRY_KINDS.get(page)
        if kinds is None:
            raise HTTPException(
                status_code=404,
                detail=s.ProblemDetail(
                    error="No such registry",
                    detail=f"{page!r} is not one of: {', '.join(registries.REGISTRY_KINDS)}.",
                ).model_dump(),
            )
        state.refresh()
        grouped: dict[str, list[s.RegistryVersion]] = {}
        for name, version, runs, first, last in state.index.registry(kinds):
            grouped.setdefault(name, []).append(
                s.RegistryVersion(
                    version=version,
                    runs=runs,
                    first_seen=_parse_time(first),
                    last_seen=_parse_time(last),
                )
            )
        entries = [
            registries.registry_entry(page, name, versions) for name, versions in grouped.items()
        ]
        return sorted(entries, key=lambda e: e.runs, reverse=True)

    @app.get(f"{PREFIX}/registry/{{page}}/{{name}}")
    def registry_detail(page: str, name: str) -> s.RegistryDetail:
        kinds = registries.REGISTRY_KINDS.get(page)
        if kinds is None:
            raise HTTPException(status_code=404, detail="no such registry")
        state.refresh()
        versions = _versions(kinds, name)
        if not versions:
            raise HTTPException(
                status_code=404,
                detail=s.ProblemDetail(
                    error="Not found",
                    detail=f"No run used a {page[:-1] if page.endswith('s') else page} "
                    f"called {name!r}.",
                    action="Back to the list",
                    href=f"/{page}",
                ).model_dump(),
            )
        runs = state.index.runs_using(kinds, name)
        return s.RegistryDetail(
            kind=page,
            name=name,
            runs=sum(v.runs for v in versions),
            versions=sorted(versions, key=lambda v: v.last_seen or v.version, reverse=True),
            used_by=state.index.used_with(kinds, name, registries.REGISTRY_KINDS["agents"]),
            recent_runs=runs[:25],
        )

    # Evaluations and regression (UI §26, §27) ---------------------------------
    def _reports() -> list[Any]:
        from rewyn.evaluation.regression import RegressionReport

        return RegressionReport.history(home=state.store.home)

    @app.get(f"{PREFIX}/evaluations")
    def evaluations() -> s.EvaluationsView:
        """Evaluator score distributions (UI §27)."""
        state.refresh()
        grouped: dict[str, list[tuple[float, bool, str]]] = {}
        for evaluator, value, passed, at in state.index.evaluator_scores():
            grouped.setdefault(evaluator, []).append((value, passed, at))
        return s.EvaluationsView(
            evaluators=[
                registries.evaluator_summary(
                    name,
                    [value for value, _, _ in rows],
                    [passed for _, passed, _ in rows],
                    _parse_time(max(at for _, _, at in rows)),
                )
                for name, rows in sorted(grouped.items())
            ],
            scores=sum(len(rows) for rows in grouped.values()),
            runs_scored=state.index.scored_runs(),
        )

    @app.get(f"{PREFIX}/regression")
    def list_reports(dataset: str | None = None) -> list[s.RegressionSummary]:
        """Regression runs, newest first (UI §26)."""
        return [
            registries.report_summary(report)
            for report in _reports()
            if dataset is None or report.dataset == dataset
        ]

    @app.get(f"{PREFIX}/regression/{{report_id}}")
    def get_report(report_id: str) -> s.RegressionDetail:
        from rewyn.evaluation.regression import RegressionError, RegressionReport

        try:
            report = RegressionReport.load(report_id, home=state.store.home)
        except RegressionError as exc:
            raise HTTPException(
                status_code=404,
                detail=s.ProblemDetail(
                    error="Report not found",
                    detail=str(exc),
                    action="Browse regression runs",
                    href="/regression",
                ).model_dump(),
            ) from exc
        baseline = None
        if report.baseline_id:
            try:
                baseline = RegressionReport.load(report.baseline_id, home=state.store.home)
            except RegressionError:
                baseline = None
        return registries.report_detail(report, baseline)

    @app.post(f"{PREFIX}/experiments:plan")
    def plan_experiment(body: s.ExperimentRequest) -> s.ExperimentPlan:
        """How to run an experiment the console cannot run itself (UI §26, §48)."""
        return registries.experiment_plan(body)

    # Live and approvals (UI §34, §35, §53) ------------------------------------
    @app.get(f"{PREFIX}/live")
    def live_stream(request: Request) -> StreamingResponse:
        """Server-sent frames of everything in flight (UI §34, §53)."""

        def running() -> list[str]:
            state.refresh(force=True)
            page = state.index.search(RunQuery(status="running", limit=25))
            return [run.id for run in page.runs]

        return StreamingResponse(
            live.stream_live(state.store, running, disconnected=request.is_disconnected),
            media_type="text/event-stream",
            headers={"cache-control": "no-store", "x-accel-buffering": "no"},
        )

    @app.get(f"{PREFIX}/live/runs")
    def live_runs() -> list[s.LiveRun]:
        """A snapshot of what is in flight, for clients that cannot stream."""
        state.refresh(force=True)
        page = state.index.search(RunQuery(status="running", limit=25))
        return [view for view in (live.live_run(state.store, r.id) for r in page.runs) if view]

    @app.get(f"{PREFIX}/runs/{{run_id}}/stream")
    def run_stream(request: Request, run_id: str, after_seq: int = 0) -> StreamingResponse:
        """Server-sent frames of one run, ending when the run does (UI §53)."""
        resolved = state.store.resolve_run_id(run_id)
        return StreamingResponse(
            live.stream_run(
                state.store,
                resolved,
                after_seq=after_seq,
                disconnected=request.is_disconnected,
            ),
            media_type="text/event-stream",
            headers={"cache-control": "no-store", "x-accel-buffering": "no"},
        )

    @app.get(f"{PREFIX}/approvals")
    def approvals(pending: bool = False) -> list[s.PendingApprovalView]:
        """What is waiting for a person (UI §35)."""
        from rewyn.human.inbox import ApprovalInbox

        return [
            _approval_view(item)
            for item in ApprovalInbox(state.store.home).list(pending_only=pending)
        ]

    @app.post(f"{PREFIX}/approvals/{{request_id}}/decision")
    def decide_approval(request_id: str, body: s.ApprovalDecisionRequest) -> s.PendingApprovalView:
        """Approve or reject. The waiting agent picks this up (UI §35)."""
        from rewyn.human.inbox import ApprovalError, ApprovalInbox

        inbox = ApprovalInbox(state.store.home)
        try:
            answered = inbox.decide(
                request_id,
                approved=body.approved,
                by=body.by,
                reason=body.reason,
                correction=body.correction,
            )
        except ApprovalError as exc:
            raise HTTPException(
                status_code=409 if "already" in str(exc) else 404,
                detail=s.ProblemDetail(
                    error="Approval unavailable",
                    detail=str(exc),
                    action="Back to approvals",
                    href="/live",
                ).model_dump(),
            ) from exc
        return _approval_view(answered)

    # Intelligence: dependencies, drift, cost, releases (UI §28, §31-§33, §40, §43)
    @app.get(f"{PREFIX}/dependencies")
    def dependencies(agent: str) -> s.DependencyMap:
        """What an agent depends on, and what those depend on (UI §31)."""
        state.refresh()
        kinds = registries.REGISTRY_KINDS["agents"]
        runs = state.index.runs_using(kinds, agent, limit=25)
        if not runs:
            raise HTTPException(
                status_code=404,
                detail=s.ProblemDetail(
                    error="Nothing to map",
                    detail=(
                        f"No run used an agent or graph called {agent!r}, so there are no "
                        "recorded dependencies to draw."
                    ),
                    action="Browse agents",
                    href="/agents",
                ).model_dump(),
            )
        manifests = [state.store.read_manifest(run.id) for run in runs]
        owned: dict[str, list[str]] = {}
        for run in runs[:5]:
            recorded = state.recorded(run.id)
            mcp_payloads = [e.payload for e in recorded.events_of(EventType.MCP_TOOLS_DISCOVERED)]
            skill_payloads = [e.payload for e in recorded.events_of(EventType.SKILL_LOADED)]
            for source in (
                intelligence.mcp_tool_ownership(mcp_payloads),
                intelligence.skill_tool_ownership(skill_payloads),
            ):
                for owner, names in source.items():
                    owned.setdefault(owner, []).extend(names)
        return intelligence.dependency_map(agent, manifests, owned=owned)

    @app.get(f"{PREFIX}/drift")
    def drift(agent: str, window: Annotated[int, Query(ge=1, le=200)] = 10) -> s.DriftView:
        """Did behaviour move, and what moved underneath it (UI §32)?"""
        state.refresh()
        kinds = registries.REGISTRY_KINDS["agents"]
        runs = state.index.runs_using(kinds, agent, limit=window * 2)
        if len(runs) < 2:
            raise HTTPException(
                status_code=409,
                detail=s.ProblemDetail(
                    error="Not enough history",
                    detail=(
                        f"Drift compares two windows of runs and {agent!r} has "
                        f"{len(runs)}. Run it again and this fills in."
                    ),
                    action="Browse runs",
                    href=f"/runs?agent={agent}",
                ).model_dump(),
            )
        current_runs = runs[: max(1, len(runs) // 2)]
        baseline_runs = runs[len(current_runs) :]
        return intelligence.drift_view(
            agent,
            [state.store.read_manifest(run.id) for run in baseline_runs],
            [state.store.read_manifest(run.id) for run in current_runs],
            baseline_runs=baseline_runs,
            current_runs=current_runs,
        )

    @app.get(f"{PREFIX}/cost")
    def cost(group_by: str = "agent", environment: str | None = None) -> s.CostView:
        """Where the money went, and what a successful task cost (UI §33)."""
        state.refresh()
        categories = state.index.cost_categories(environment=environment)
        if group_by == "time":
            rows = state.index.cost_by_day(environment=environment)
        elif group_by in ("tool", "skill", "mcp"):
            rows = state.index.cost_by_dependency(
                registries.REGISTRY_KINDS[
                    {"tool": "tools", "skill": "skills", "mcp": "mcp"}[group_by]
                ]
            )
        else:
            try:
                rows = state.index.cost_by(group_by, environment=environment)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=s.ProblemDetail(
                        error="Cannot group by that",
                        detail=str(exc),
                        action="Group by agent",
                        href="/cost?group_by=agent",
                    ).model_dump(),
                ) from exc
        return intelligence.cost_view(group_by, rows, categories)

    @app.get(f"{PREFIX}/releases")
    def releases(agent: str | None = None) -> list[s.ReleaseView]:
        """Each version, and whether it is safe to deploy (UI §43)."""
        state.refresh()
        kinds = registries.REGISTRY_KINDS["agents"]
        reports = _reports()
        found: list[s.ReleaseView] = []
        names = (
            [agent] if agent else sorted({name for name, _, _, _, _ in state.index.registry(kinds)})
        )
        for name in names:
            versions = sorted(
                {
                    version
                    for entry, version, _, _, _ in state.index.registry(kinds)
                    if entry == name
                }
            )
            per_version: dict[str, list[s.RunSummary]] = {version: [] for version in versions}
            manifests: dict[str, list[Any]] = {version: [] for version in versions}
            for run in state.index.runs_using(kinds, name, limit=200):
                manifest = state.store.read_manifest(run.id)
                for dependency in manifest.dependencies:
                    matched = dependency.kind in kinds and dependency.name == name
                    if matched and dependency.version in per_version:
                        per_version[dependency.version].append(run)
                        manifests[dependency.version].append(manifest)
            ordered = [v for v in versions if per_version[v]]
            for index, version in enumerate(ordered):
                previous = ordered[index - 1] if index else None
                matching = [r for r in reports if name in (r.target or "")]
                report = matching[0] if matching else None
                baseline = next(
                    (r for r in reports if report is not None and r.id == report.baseline_id), None
                )
                found.append(
                    intelligence.release_view(
                        name,
                        version,
                        previous_version=previous,
                        manifests=manifests[version],
                        previous_manifests=manifests[previous] if previous else [],
                        runs=per_version[version],
                        report=report,
                        baseline_report=baseline,
                    )
                )
        promoted = state.collab.promotions()
        return sorted(
            (r.model_copy(update={"promoted": promoted.get(r.id)}) for r in found),
            key=lambda r: (r.application, r.version),
            reverse=True,
        )

    def _one_release(release_id: str) -> s.ReleaseView:
        try:
            application, version = intelligence.split_release_id(release_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=s.ProblemDetail(
                    error="That is not a release id",
                    detail=str(exc),
                    action="Browse releases",
                    href="/releases",
                ).model_dump(),
            ) from exc
        for release in releases(agent=application):
            if release.version == version:
                return release
        raise HTTPException(
            status_code=404,
            detail=s.ProblemDetail(
                error="Release not found",
                detail=(
                    f"No run recorded {application!r} at version {version!r}. A release "
                    "exists once a run has used that version of the agent."
                ),
                action="Browse releases",
                href="/releases",
            ).model_dump(),
        )

    @app.get(f"{PREFIX}/releases/{{release_id}}")
    def get_release(release_id: str) -> s.ReleaseView:
        """One version, what changed in it, and whether it is safe (UI §43)."""
        return _one_release(release_id)

    @app.post(f"{PREFIX}/releases/{{release_id}}/promote")
    def promote_release(release_id: str, body: s.PromoteRequest) -> s.ReleaseView:
        """Authorize a version for an environment (UI §43).

        Refused server-side when the gate has not passed, so the answer does
        not depend on whether the button was disabled in the browser (UI §54).
        The console records the decision and the evidence behind it; deploying
        is still the pipeline's job, and it reads this rather than guessing.
        """
        release = _one_release(release_id)
        refusal = intelligence.refuse_promotion(release)
        if refusal is not None:
            raise HTTPException(
                status_code=409,
                detail=s.ProblemDetail(
                    error="This version is not safe to deploy",
                    detail=refusal,
                    action=(
                        "Open the regression report"
                        if release.report_id
                        else "Run the regression suite"
                    ),
                    href=(
                        f"/regression/{release.report_id}" if release.report_id else "/regression"
                    ),
                ).model_dump(),
            )
        record = state.collab.record_promotion(
            intelligence.promotion_record(release, body, by=collaboration.local_author())
        )
        return release.model_copy(update={"promoted": record})

    @app.get(f"{PREFIX}/experiments")
    def experiments(dataset: str | None = None) -> list[s.ExperimentView]:
        """Control against variants, grouped by dataset (UI §28)."""
        grouped: dict[str, list[Any]] = {}
        for report in _reports():
            if dataset is not None and report.dataset != dataset:
                continue
            grouped.setdefault(report.dataset, []).append(report)
        return [
            intelligence.experiment_view(name, reports)
            for name, reports in sorted(grouped.items())
            if len(reports) > 1 or dataset is not None
        ]

    @app.get(f"{PREFIX}/notifications")
    def notifications(limit: Annotated[int, Query(ge=1, le=100)] = 25) -> list[s.NotificationView]:
        """Meaningful AI events, deduplicated by cause (UI §40)."""
        state.refresh()
        page = state.index.search(RunQuery(limit=200))
        versions = state.index.dependency_versions([run.id for run in page.runs])
        drift_views: list[s.DriftView] = []
        kinds = registries.REGISTRY_KINDS["agents"]
        for name in sorted({run.agent for run in page.runs if run.agent})[:10]:
            runs = state.index.runs_using(kinds, name, limit=20)
            if len(runs) < 2:
                continue
            current_runs = runs[: max(1, len(runs) // 2)]
            baseline_runs = runs[len(current_runs) :]
            drift_views.append(
                intelligence.drift_view(
                    name,
                    [state.store.read_manifest(run.id) for run in baseline_runs],
                    [state.store.read_manifest(run.id) for run in current_runs],
                    baseline_runs=baseline_runs,
                    current_runs=current_runs,
                )
            )
        return intelligence.notifications(
            reports=_reports(),
            drift=drift_views,
            incidents=incidents.detect(page.runs),
            dependency_changes=aggregates.recent_changes(page.runs, versions),
        )[:limit]

    # The control room: incidents, collaboration, narrative, workspace ---------
    def _incident_inputs() -> tuple[
        list[s.RunSummary], list[s.RecentChange], list[Any], dict[str, dict[str, Any]]
    ]:
        state.refresh()
        page = state.index.search(RunQuery(limit=500))
        versions = state.index.dependency_versions([run.id for run in page.runs])
        return (
            page.runs,
            aggregates.recent_changes(page.runs, versions, limit=50),
            _reports(),
            state.collab.incident_states(),
        )

    def _incident_detail(
        incident: s.Incident,
        runs: Sequence[s.RunSummary],
        changes: Sequence[s.RecentChange],
        reports: Sequence[Any],
        states: Mapping[str, Mapping[str, Any]],
    ) -> s.IncidentDetail:
        by_id = {run.id: run for run in runs}
        affected = [by_id[run_id] for run_id in incident.run_ids if run_id in by_id]
        return incidents.detail(
            incident,
            runs=affected,
            changes=changes,
            reports=reports,
            state=states.get(incident.id),
            comments=state.collab.comments(f"incident:{incident.id}"),
            agent_runs=[r for r in runs if incident.agent and r.agent == incident.agent],
        )

    @app.get(f"{PREFIX}/incidents")
    def list_incidents(status: str | None = None) -> list[s.IncidentDetail]:
        """Every failure cluster the runs show, newest first (UI §36)."""
        runs, changes, reports, states = _incident_inputs()
        found = [
            _incident_detail(incident, runs, changes, reports, states)
            for incident in incidents.detect(runs)
        ]
        if status is not None:
            found = [incident for incident in found if incident.status == status]
        return sorted(found, key=lambda i: i.started_at or i.last_seen or utcnow(), reverse=True)

    def _one_incident(incident_id: str) -> s.IncidentDetail:
        runs, changes, reports, states = _incident_inputs()
        for incident in incidents.detect(runs):
            if incident.id == incident_id:
                return _incident_detail(incident, runs, changes, reports, states)
        raise HTTPException(
            status_code=404,
            detail=s.ProblemDetail(
                error="Incident not found",
                detail=(
                    f"No current failure cluster has the id {incident_id!r}. An incident "
                    "exists while its runs do: if they aged out of the window, or the "
                    "failures stopped, it is no longer detected."
                ),
                action="Open incidents",
                href="/incidents",
            ).model_dump(),
        )

    @app.get(f"{PREFIX}/incidents/{{incident_id}}")
    def get_incident(incident_id: str) -> s.IncidentDetail:
        """One incident, its timeline and everyone working on it (UI §36)."""
        return _one_incident(incident_id)

    @app.post(f"{PREFIX}/incidents/{{incident_id}}")
    def update_incident(incident_id: str, body: s.IncidentUpdate) -> s.IncidentDetail:
        """Take an incident, or move it along its timeline (UI §36, §45)."""
        _one_incident(incident_id)
        state.collab.update_incident(incident_id, body, author=collaboration.local_author())
        return _one_incident(incident_id)

    @app.get(f"{PREFIX}/comments")
    def list_comments(subject: str | None = None) -> list[s.IncidentComment]:
        """The discussion on a run, an incident, a dataset or an agent (UI §45)."""
        try:
            return state.collab.comments(subject)
        except ValueError as exc:
            raise _bad_subject(exc) from exc

    @app.post(f"{PREFIX}/comments", status_code=201)
    def add_comment(body: s.CommentRequest) -> s.IncidentComment:
        try:
            return state.collab.add_comment(body, author=collaboration.local_author())
        except ValueError as exc:
            raise _bad_subject(exc) from exc

    @app.post(f"{PREFIX}/comments/{{comment_id}}/resolve")
    def resolve_comment(comment_id: str, resolved: bool = True) -> s.IncidentComment:
        try:
            return state.collab.resolve_comment(comment_id, resolved=resolved)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=s.ProblemDetail(
                    error="Comment not found",
                    detail=f"No comment with the id {comment_id!r}.",
                ).model_dump(),
            ) from exc

    @app.get(f"{PREFIX}/views")
    def list_views(screen: str | None = None) -> list[s.SavedView]:
        """Filters somebody kept, so a question asked once can be asked again (UI §45)."""
        return state.collab.views(screen.strip("/") if screen else None)

    @app.post(f"{PREFIX}/views", status_code=201)
    def add_view(body: s.SavedViewRequest) -> s.SavedView:
        try:
            return state.collab.add_view(body, author=collaboration.local_author())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=s.ProblemDetail(
                    error="That view cannot be saved", detail=str(exc)
                ).model_dump(),
            ) from exc

    @app.delete(f"{PREFIX}/views/{{view_id}}", status_code=204)
    def delete_view(view_id: str) -> Response:
        if not state.collab.delete_view(view_id):
            raise HTTPException(
                status_code=404,
                detail=s.ProblemDetail(
                    error="Saved view not found",
                    detail=f"No saved view with the id {view_id!r}.",
                ).model_dump(),
            )
        return Response(status_code=204)

    @app.post(f"{PREFIX}/runs/{{run_id}}/explain")
    async def explain_difference(recorded: Recorded, against: str) -> s.NarrativeView:
        """ "Explain difference", in prose, from the comparison's evidence (UI §23)."""
        from rewyn.replay.diff import diff as diff_runs_impl

        other = state.recorded(against)
        view = workspaces.diff_view(diff_runs_impl(other, recorded), other, recorded)
        return await explain.explain(view)

    @app.get(f"{PREFIX}/workspace")
    def workspace(agent: str, environment: str | None = None) -> s.WorkspaceView:
        """Config → run → inspect → replay → evaluate → regression (UI §37, §60)."""
        detail = get_agent(agent)
        latest = detail.recent_runs[0] if detail.recent_runs else None
        counts: s.PanelCounts | None = None
        plan: s.ReplayPlan | None = None
        if latest is not None:
            run = state.recorded(latest.id)
            counts = projections.run_detail(run).panels
            plan = workspaces.plan_replay(run, s.ReplayRequest())
        page = state.index.search(RunQuery(agent=agent, limit=200))
        release = next(iter(releases(agent=agent)), None)
        return lifecycle.workspace(
            detail,
            latest=latest,
            counts=counts,
            replay=plan,
            reports=detail.reports,
            incidents=[i for i in incidents.detect(page.runs) if i.agent in (None, agent)],
            release=release,
            environment=environment,
        )

    _mount_static(app, assets)
    return app


def _names(index: RunIndex, kinds: tuple[str, ...]) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for name, version, _ in index.dependency_names(kinds):
        seen.setdefault(name, version)
    return list(seen.items())


def _mount_static(app: FastAPI, assets: Path) -> None:
    """Serve the built frontend, or explain how to build it (UI §47, §48)."""
    index_file = assets / "index.html"
    if index_file.exists():
        # The bundle is a single-page app: real files are served as they are,
        # and every other path falls back to the shell so deep links work.
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = assets / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_file)

        return

    @app.get("/", include_in_schema=False)
    def unbuilt() -> HTMLResponse:
        return HTMLResponse(UNBUILT_PAGE, status_code=200)


UNBUILT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Rewyn console</title>
<style>
 :root { color-scheme: dark light; }
 body { font: 14px/1.5 ui-sans-serif, system-ui, sans-serif; margin: 0;
        display: grid; place-items: center; min-height: 100vh;
        background: #0b0d10; color: #e6e8eb; }
 main { max-width: 34rem; padding: 2rem; }
 h1 { font-size: 1.25rem; margin: 0 0 .5rem; }
 p { color: #9aa4b2; }
 code { font-family: ui-monospace, SFMono-Regular, monospace; background: #171a1f;
        padding: .15rem .35rem; border-radius: .25rem; color: #e6e8eb; }
 a { color: #7aa2f7; }
</style></head>
<body><main>
<h1>The console API is running; the interface is not built yet.</h1>
<p>Build the frontend once, then reload this page:</p>
<p><code>make build-web</code></p>
<p>The API is live in the meantime &mdash;
<a href="/v1/docs">/v1/docs</a> lists every endpoint, and
<a href="/v1/runs">/v1/runs</a> returns your recorded runs.</p>
</main></body></html>
"""


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 4400,
    store: LocalStore | None = None,
    reload: bool = False,
) -> None:
    """Run the local console (blocking)."""
    import uvicorn

    uvicorn.run(build_app(store), host=host, port=port, reload=reload, log_level="warning")


def iter_features() -> Iterator[str]:
    yield from FEATURES
