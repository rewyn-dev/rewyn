"""Replay and compare: the workspaces where behaviour gets changed (UI §20-§24).

P0 made an execution readable. This module makes it *actionable*: replay a
recorded run with one component swapped, compare two runs and say what
changed, and turn a run into a regression case.

Two rules from the specification shape everything here.

Nothing is claimed without evidence (UI §23). A diff returns observed
differences and, separately, hypotheses with a confidence -- the console
labels them Observed and Inference and never merges the two.

Nothing pretends (UI §21, §48). A replay is planned before it runs, and the
plan says in plain language what each of the nine controls will actually do
on this surface, including the ones that can only come from the recording.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from rewyn.core.types import new_id, utcnow
from rewyn.replay.diff import RunDiff
from rewyn.replay.recorder import RecordedRun
from rewyn.ui import projections
from rewyn.ui import schemas as s

# Which controls the replay engine can act on, and what the others mean.
_ENGINE_COMPONENTS = frozenset({"model", "tools", "context", "temperature", "system"})

ReplayMode = Literal["reconstruct", "prompt", "execute"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]

_FROM_RECORDING = {
    "prompt": (
        "The recorded requests are the prompt. Change the model, the "
        "temperature or the system instructions to vary them."
    ),
    "memory": (
        "Memory reads come from the recording. Re-executing the agent is what uses live memory."
    ),
    "skills": "The skills this run loaded come from the recording.",
    "mcp": "MCP responses come from the recording.",
}

# The dimensions §22's WHAT CHANGED? table asks for, in its order.
CHANGE_DIMENSIONS: tuple[str, ...] = (
    "model",
    "prompt",
    "context",
    "memory",
    "tools",
    "mcp",
)

# Output, cost and latency are outcomes rather than inputs: §22 gives each its
# own row with a delta, so they are not repeated in the dimension list.
_OUTCOMES = frozenset({"output", "cost", "latency"})


def _setting(components: Sequence[s.ReplayComponent], name: str) -> s.ReplayComponent:
    for component in components:
        if component.component == name:
            return component
    return s.ReplayComponent(component=name, mode="original")


def plan_replay(recorded: RecordedRun, request: s.ReplayRequest) -> s.ReplayPlan:
    """Describe the replay a request asks for, before running it (UI §21)."""
    settings = {c.component: c for c in request.components}
    model = _setting(request.components, "model")
    tools = _setting(request.components, "tools")

    mode: ReplayMode = "reconstruct"
    if model.mode == "new" and model.value:
        mode = "prompt"
    notes: list[str] = []
    if model.mode == "live" or tools.mode == "live":
        notes.append(
            "Re-executing the agent needs the application code, which the console "
            "does not have. Everything else can still be replayed from the recording."
        )

    rows: list[s.ReplayComponentPlan] = []
    for name in s.REPLAY_COMPONENTS:
        component = settings.get(name) or s.ReplayComponent(component=name, mode="original")
        supported, effect = _effect(name, component, mode)
        rows.append(
            s.ReplayComponentPlan(
                component=name,
                mode=component.mode,
                value=component.value,
                supported=supported,
                effect=effect,
            )
        )
    if mode == "reconstruct":
        notes.append(
            "Nothing is being changed, so this reproduces the run exactly from the "
            "recording: no provider is called and nothing is spent."
        )
    return s.ReplayPlan(run_id=recorded.id, mode=mode, components=rows, notes=notes)


def _effect(name: str, component: s.ReplayComponent, mode: ReplayMode) -> tuple[bool, str]:
    """What one control does, said plainly."""
    if name in _FROM_RECORDING:
        if component.mode in ("recorded", "original"):
            return True, _FROM_RECORDING[name]
        return False, f"Not changeable from the console. {_FROM_RECORDING[name]}"
    if component.mode in ("original", "recorded"):
        return True, "Comes from the recording, unchanged."
    if component.mode == "live":
        return (
            False,
            "Live execution needs the agent's own code; the console can only replay "
            "against the recording.",
        )
    if not component.value:
        return False, "Set a value to change this."
    if name == "model":
        return True, f"Every recorded request is re-issued against {component.value}."
    if name == "temperature":
        return (
            mode == "prompt",
            f"Requests are re-issued at temperature {component.value}"
            + ("." if mode == "prompt" else ", which needs a replacement model."),
        )
    if name == "system":
        return (
            mode == "prompt",
            "The system instructions are replaced and the rest of each recorded "
            "request is kept" + ("." if mode == "prompt" else ", which needs a replacement model."),
        )
    if name == "context":
        return True, f"Context is taken from {component.value!r} rather than the recording."
    return True, "Changed."


def replay_arguments(plan: s.ReplayPlan) -> dict[str, Any]:
    """Turn a plan into keyword arguments for ``rewyn.replay.areplay``."""
    settings = {row.component: row for row in plan.components}
    arguments: dict[str, Any] = {}
    model = settings.get("model")
    if model is not None and model.mode == "new" and model.value:
        arguments["model"] = model.value
    tools = settings.get("tools")
    if tools is not None and tools.mode in ("recorded", "live"):
        arguments["tools"] = tools.mode
    context = settings.get("context")
    if context is not None and context.mode == "new" and context.value:
        arguments["context"] = context.value
    temperature = settings.get("temperature")
    if temperature is not None and temperature.mode == "new" and temperature.value:
        with contextlib.suppress(ValueError):
            arguments["temperature"] = float(temperature.value)
    system = settings.get("system")
    if system is not None and system.mode == "new" and system.value:
        arguments["system"] = system.value
    return arguments


def replay_view(job: ReplayJob) -> s.ReplayView:
    """The console's view of a replay, at whatever stage it has reached."""
    result = job.result
    view = s.ReplayView(
        id=job.id,
        status=job.status,
        plan=job.plan,
        original_run_id=job.plan.run_id,
        started_at=job.started_at,
        finished_at=job.finished_at,
        problem=job.problem,
    )
    if result is None:
        return view
    return view.model_copy(
        update={
            "replay_run_id": result.run_id,
            "identical": result.identical,
            "faithful": result.faithful,
            "original_output": result.original_output,
            "output": result.output,
            "substitutions": result.substitutions,
            "original_cost": result.original_cost,
            "cost": result.cost,
            "duration_ms": result.duration_ms,
            "prompts": [
                s.PromptComparison(
                    index=p.index,
                    original_model=p.original_model,
                    replay_model=p.replay_model,
                    original_text=p.original_text,
                    replay_text=p.replay_text,
                    original_tool_calls=list(p.original_tool_calls),
                    replay_tool_calls=list(p.replay_tool_calls),
                    original_cost=p.original_cost,
                    replay_cost=p.replay_cost,
                    original_latency_ms=p.original_latency_ms,
                    replay_latency_ms=p.replay_latency_ms,
                    changed=p.changed,
                    error=p.error,
                )
                for p in result.prompts
            ],
            "mismatches": [
                s.MismatchView(
                    kind=m.kind,
                    reason=m.reason,
                    index=m.index,
                    expected=m.expected,
                    actual=m.actual,
                    detail=m.detail,
                )
                for m in result.mismatches
            ],
        }
    )


@dataclass
class ReplayJob:
    """One replay, tracked while it runs.

    Replays that only reconstruct a recording finish instantly; one that
    re-issues a transcript against a live provider does not, so the console
    starts the work and polls rather than holding a request open.
    """

    id: str
    plan: s.ReplayPlan
    status: JobStatus = "queued"
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    result: Any = None
    problem: s.ProblemDetail | None = None
    task: asyncio.Task[None] | None = None


class ReplayJobs:
    """An in-process registry of replays, newest first."""

    def __init__(self, limit: int = 64) -> None:
        self._jobs: dict[str, ReplayJob] = {}
        self._limit = limit

    def get(self, job_id: str) -> ReplayJob | None:
        return self._jobs.get(job_id)

    def all(self) -> list[ReplayJob]:
        return sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)

    def start(self, plan: s.ReplayPlan, coroutine: Any) -> ReplayJob:
        job = ReplayJob(id=new_id("replay"), plan=plan, status="running")
        self._jobs[job.id] = job
        self._trim()
        job.task = asyncio.create_task(self._run(job, coroutine))
        return job

    async def _run(self, job: ReplayJob, coroutine: Any) -> None:
        try:
            job.result = await coroutine
            job.status = "succeeded"
        except Exception as exc:
            job.status = "failed"
            job.problem = _replay_problem(exc, job.plan)
        finally:
            job.finished_at = utcnow()

    def _trim(self) -> None:
        if len(self._jobs) <= self._limit:
            return
        for job in self.all()[self._limit :]:
            self._jobs.pop(job.id, None)


def _replay_problem(exc: BaseException, plan: s.ReplayPlan) -> s.ProblemDetail:
    """A replay failure the reader can act on (UI §48).

    The spec's own example is the shape to hit: say what broke, then say that
    the run can still be reproduced from the recording, and offer that.
    """
    detail = f"{type(exc).__name__}: {exc}"
    return s.ProblemDetail(
        error="Replay failed",
        detail=(
            f"{detail}\n\nThe original run can still be reproduced exactly using the "
            "recorded responses."
        ),
        action="Replay with recorded responses",
        href=f"/runs/{plan.run_id}?tab=replay&mode=reconstruct",
    )


# Compare ----------------------------------------------------------------------
def _percent(before: float, after: float) -> float | None:
    if before <= 0:
        return None
    return (after - before) / before * 100.0


def diff_view(diff: RunDiff, before: RecordedRun, after: RecordedRun) -> s.DiffView:
    """The compare workspace's document (UI §13, §22, §23)."""
    summary_a = projections.run_summary(before.manifest)
    summary_b = projections.run_summary(after.manifest)
    counts: dict[str, int] = {}
    for difference in diff.differences:
        counts[difference.dimension.value] = counts.get(difference.dimension.value, 0) + 1
    dimensions = [
        s.DimensionSummary(
            dimension=name, changed=counts.get(name, 0) > 0, differences=counts.get(name, 0)
        )
        for name in CHANGE_DIMENSIONS
    ]
    for name, total in sorted(counts.items()):
        if name not in CHANGE_DIMENSIONS and name not in _OUTCOMES:
            dimensions.append(s.DimensionSummary(dimension=name, changed=True, differences=total))
    quality = (
        summary_b.eval_score - summary_a.eval_score
        if summary_a.eval_score is not None and summary_b.eval_score is not None
        else None
    )
    return s.DiffView(
        run_a=diff.run_a,
        run_b=diff.run_b,
        summary_a=summary_a,
        summary_b=summary_b,
        identical=diff.identical,
        dimensions=dimensions,
        differences=[
            s.DifferenceView(
                dimension=d.dimension.value,
                field=d.field,
                kind=d.kind.value,
                before=d.before,
                after=d.after,
                delta=d.delta,
                description=d.describe(),
            )
            for d in diff.differences
        ],
        explanations=[
            s.ExplanationView(
                observed=e.observed.value,
                cause=e.cause.value if e.cause is not None else None,
                confidence=e.confidence,
                rationale=e.rationale,
                description=e.describe(),
            )
            for e in diff.explanations
        ],
        output_changed=before.output != after.output,
        cost_delta_percent=_percent(summary_a.cost, summary_b.cost),
        latency_delta_percent=_percent(summary_a.duration_ms, summary_b.duration_ms),
        quality_delta=quality,
    )


# Datasets ---------------------------------------------------------------------
def case_view(item: Any) -> s.DatasetCaseView:
    metadata: Mapping[str, Any] = item.metadata or {}
    return s.DatasetCaseView(
        id=item.id,
        input=item.input,
        expected=item.expected,
        tags=list(item.tags),
        source_run_id=item.source_run_id,
        evaluator=metadata.get("evaluator"),
        severity=metadata.get("severity"),
        created_at=item.created_at,
    )


def dataset_summary(dataset: Any) -> s.DatasetSummary:
    return s.DatasetSummary(
        name=dataset.name,
        version=dataset.version,
        description=dataset.description,
        cases=len(dataset.items),
        tags=list(dataset.tags),
        updated_at=dataset.updated_at,
    )


def dataset_detail(dataset: Any) -> s.DatasetDetail:
    return s.DatasetDetail(
        **dataset_summary(dataset).model_dump(),
        items=[case_view(item) for item in dataset.items],
    )
