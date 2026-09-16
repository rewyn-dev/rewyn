"""Execution diff (spec §28).

Two runs are compared across every dimension the spec lists -- model,
prompt, context, memory, tools, MCP, skills, graph, loop, state, output,
cost, latency -- and the result keeps two things strictly apart:

``differences``
    What actually changed. Every entry is a fact derived from the two runs.

``explanations``
    What *might* account for an observed change. Every entry is a hypothesis
    with a confidence, and never claims to be an observation.

Confusing the two is how teams end up chasing the wrong regression, so the
API never merges them into one list.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import EventType
from rewyn.core.schema import canonical_json
from rewyn.core.types import JSONObject
from rewyn.replay.recorder import RecordedRun


class Dimension(StrEnum):
    """The comparison dimensions listed in spec §28."""

    MODEL = "model"
    PROMPT = "prompt"
    CONTEXT = "context"
    MEMORY = "memory"
    TOOLS = "tools"
    MCP = "mcp"
    SKILLS = "skills"
    GRAPH = "graph"
    LOOP = "loop"
    STATE = "state"
    OUTPUT = "output"
    COST = "cost"
    LATENCY = "latency"


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


class Difference(BaseModel):
    """One observed change between two runs. A fact, never a hypothesis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: Dimension
    field: str
    kind: ChangeKind
    before: Any = None
    after: Any = None
    delta: float | None = None

    def describe(self) -> str:
        if self.kind is ChangeKind.ADDED:
            return f"{self.dimension.value}.{self.field}: added {self.after!r}"
        if self.kind is ChangeKind.REMOVED:
            return f"{self.dimension.value}.{self.field}: removed {self.before!r}"
        head = f"{self.dimension.value}.{self.field}"
        if self.delta is not None:
            return f"{head}: {self.before} -> {self.after} ({self.delta:+g})"
        return f"{head}: {self.before!r} -> {self.after!r}"


class Explanation(BaseModel):
    """A hypothesis linking an input change to an observed outcome change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed: Dimension
    cause: Dimension | None = None
    """The dimension that may explain the change, or ``None`` when nothing upstream changed."""

    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str

    def describe(self) -> str:
        if self.cause is None:
            return (
                f"{self.observed.value} changed with no upstream change "
                f"({self.confidence:.0%} confidence): {self.rationale}"
            )
        return (
            f"{self.observed.value} may have changed because {self.cause.value} changed "
            f"({self.confidence:.0%} confidence): {self.rationale}"
        )


class RunDiff(BaseModel):
    """The result of comparing two runs."""

    model_config = ConfigDict(extra="forbid")

    run_a: str
    run_b: str
    differences: list[Difference] = Field(default_factory=list)
    explanations: list[Explanation] = Field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not self.differences

    @property
    def dimensions(self) -> list[Dimension]:
        seen: list[Dimension] = []
        for difference in self.differences:
            if difference.dimension not in seen:
                seen.append(difference.dimension)
        return seen

    def of(self, dimension: Dimension) -> list[Difference]:
        return [d for d in self.differences if d.dimension is dimension]

    def summary(self) -> JSONObject:
        return {
            "run_a": self.run_a,
            "run_b": self.run_b,
            "identical": self.identical,
            "differences": len(self.differences),
            "dimensions": [d.value for d in self.dimensions],
            "explanations": len(self.explanations),
        }


# Profiles ---------------------------------------------------------------------
def _dependency_map(recorded: RecordedRun, kind: str) -> dict[str, str]:
    return {
        d.name: d.fingerprint or d.version for d in recorded.manifest.dependencies if d.kind == kind
    }


def _prompt_profile(recorded: RecordedRun) -> dict[str, Any]:
    calls = recorded.model_calls
    return {
        "count": len(calls),
        "system": next(
            (m.text for c in calls[:1] for m in c.messages if m.role.value == "system"), ""
        ),
        "first_request": calls[0].request_fingerprint if calls else None,
        "fingerprints": [c.request_fingerprint for c in calls],
    }


def _context_profile(recorded: RecordedRun) -> dict[str, Any]:
    assembled = recorded.events_of(EventType.CONTEXT_ASSEMBLED)
    retrieved = recorded.events_of(EventType.CONTEXT_RETRIEVED)
    return {
        "assemblies": len(assembled),
        "fingerprint": assembled[-1].payload.get("fingerprint") if assembled else None,
        "sources": sorted({str(e.payload.get("source")) for e in retrieved}),
        "included_tokens": assembled[-1].payload.get("decision", {}).get("used_tokens")
        if assembled
        else None,
        "dropped": assembled[-1].payload.get("decision", {}).get("dropped") if assembled else None,
    }


def _memory_profile(recorded: RecordedRun) -> dict[str, Any]:
    reads = recorded.events_of(EventType.MEMORY_READ)
    writes = recorded.events_of(EventType.MEMORY_WRITE)
    return {
        "providers": sorted(_dependency_map(recorded, "memory")),
        "reads": len(reads),
        "writes": len(writes),
        "read_hits": sum(int(e.payload.get("count") or 0) for e in reads),
    }


def _tool_profile(recorded: RecordedRun) -> dict[str, Any]:
    sequence = [c.name for c in recorded.tool_calls]
    results = {f"{c.name}#{i}": canonical_json(c.result) for i, c in enumerate(recorded.tool_calls)}
    return {
        "registered": _dependency_map(recorded, "tool"),
        "sequence": sequence,
        "calls": len(sequence),
        "errors": sum(1 for c in recorded.tool_calls if c.is_error),
        "results": results,
    }


def _graph_profile(recorded: RecordedRun) -> dict[str, Any]:
    nodes = recorded.events_of(EventType.GRAPH_NODE_STARTED)
    failures = recorded.events_of(EventType.GRAPH_NODE_FAILED)
    return {
        "graphs": sorted(_dependency_map(recorded, "graph")),
        "path": [str(e.payload.get("node")) for e in nodes],
        "failed": [str(e.payload.get("node")) for e in failures],
    }


def _loop_profile(recorded: RecordedRun) -> dict[str, Any]:
    started = recorded.events_of(EventType.AGENT_LOOP_STARTED)
    finished = recorded.events_of(EventType.AGENT_LOOP_FINISHED)
    return {
        "strategy": started[-1].payload.get("strategy") if started else None,
        "budget": started[-1].payload.get("budget") if started else None,
        "iterations": finished[-1].payload.get("iterations") if finished else 0,
        "stop_reason": finished[-1].payload.get("stop_reason") if finished else None,
    }


def _state_profile(recorded: RecordedRun) -> dict[str, Any]:
    updates = recorded.events_of(EventType.STATE_UPDATED)
    keys: set[str] = set()
    for event in updates:
        changed = event.payload.get("keys") or event.payload.get("changed") or []
        if isinstance(changed, list):
            keys.update(str(k) for k in changed)
    return {"updates": len(updates), "keys": sorted(keys)}


def _profile(recorded: RecordedRun) -> dict[Dimension, dict[str, Any]]:
    manifest = recorded.manifest
    return {
        Dimension.MODEL: _dependency_map(recorded, "model"),
        Dimension.PROMPT: _prompt_profile(recorded),
        Dimension.CONTEXT: _context_profile(recorded),
        Dimension.MEMORY: _memory_profile(recorded),
        Dimension.TOOLS: _tool_profile(recorded),
        Dimension.MCP: _dependency_map(recorded, "mcp_server"),
        Dimension.SKILLS: _dependency_map(recorded, "skill"),
        Dimension.GRAPH: _graph_profile(recorded),
        Dimension.LOOP: _loop_profile(recorded),
        Dimension.STATE: _state_profile(recorded),
        Dimension.OUTPUT: {
            "output": manifest.output,
            "status": manifest.status.value,
            "error": manifest.error,
        },
        Dimension.COST: {
            "total": round(manifest.cost.total, 6),
            "model": round(manifest.cost.model, 6),
            "input_tokens": manifest.usage.input_tokens,
            "output_tokens": manifest.usage.output_tokens,
            "model_calls": manifest.usage.model_calls,
        },
        Dimension.LATENCY: {"duration_ms": round(manifest.duration_ms or 0.0)},
    }


_NUMERIC = (int, float)


def _compare(dimension: Dimension, before: Any, after: Any, prefix: str = "") -> list[Difference]:
    differences: list[Difference] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            field = f"{prefix}{key}"
            if key not in before:
                differences.append(
                    Difference(
                        dimension=dimension,
                        field=field,
                        kind=ChangeKind.ADDED,
                        after=after[key],
                    )
                )
            elif key not in after:
                differences.append(
                    Difference(
                        dimension=dimension,
                        field=field,
                        kind=ChangeKind.REMOVED,
                        before=before[key],
                    )
                )
            else:
                differences.extend(_compare(dimension, before[key], after[key], f"{field}."))
        return differences
    if before == after:
        return differences
    field = prefix.rstrip(".") or "value"
    delta = None
    if isinstance(before, _NUMERIC) and isinstance(after, _NUMERIC):
        if isinstance(before, bool) or isinstance(after, bool):
            delta = None
        else:
            delta = float(after) - float(before)
    differences.append(
        Difference(
            dimension=dimension,
            field=field,
            kind=ChangeKind.CHANGED,
            before=before,
            after=after,
            delta=delta,
        )
    )
    return differences


# Causal hypotheses -------------------------------------------------------------
_INPUT_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension.MODEL,
    Dimension.PROMPT,
    Dimension.CONTEXT,
    Dimension.MEMORY,
    Dimension.TOOLS,
    Dimension.MCP,
    Dimension.SKILLS,
    Dimension.GRAPH,
    Dimension.LOOP,
    Dimension.STATE,
)

_OUTCOME_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension.OUTPUT,
    Dimension.COST,
    Dimension.LATENCY,
)

_CONFIDENCE: dict[tuple[Dimension, Dimension], tuple[float, str]] = {
    (Dimension.OUTPUT, Dimension.MODEL): (0.8, "a different model produces different text"),
    (Dimension.OUTPUT, Dimension.PROMPT): (0.8, "the prompt sent to the model changed"),
    (Dimension.OUTPUT, Dimension.CONTEXT): (0.7, "the assembled context changed"),
    (Dimension.OUTPUT, Dimension.TOOLS): (0.7, "tool results feed the model's answer"),
    (Dimension.OUTPUT, Dimension.SKILLS): (0.6, "skill instructions steer the answer"),
    (Dimension.OUTPUT, Dimension.MEMORY): (0.5, "recalled memories change the prompt"),
    (Dimension.OUTPUT, Dimension.MCP): (0.5, "MCP tools returned different data"),
    (Dimension.OUTPUT, Dimension.GRAPH): (0.6, "a different path through the graph ran"),
    (Dimension.OUTPUT, Dimension.LOOP): (0.5, "the loop stopped at a different point"),
    (Dimension.OUTPUT, Dimension.STATE): (0.4, "state carried into the prompt changed"),
    (Dimension.COST, Dimension.MODEL): (0.9, "pricing and token efficiency are per-model"),
    (Dimension.COST, Dimension.PROMPT): (0.7, "prompt length drives input tokens"),
    (Dimension.COST, Dimension.CONTEXT): (0.7, "context size drives input tokens"),
    (Dimension.COST, Dimension.LOOP): (0.7, "more iterations mean more model calls"),
    (Dimension.COST, Dimension.TOOLS): (0.5, "tool results are appended to the prompt"),
    (Dimension.COST, Dimension.MEMORY): (0.4, "recalled memories lengthen the prompt"),
    (Dimension.COST, Dimension.SKILLS): (0.4, "skill instructions lengthen the prompt"),
    (Dimension.LATENCY, Dimension.MODEL): (0.7, "providers differ in speed"),
    (Dimension.LATENCY, Dimension.TOOLS): (0.6, "tool calls dominate wall clock time"),
    (Dimension.LATENCY, Dimension.LOOP): (0.6, "more iterations take longer"),
    (Dimension.LATENCY, Dimension.MCP): (0.5, "MCP round trips add latency"),
    (Dimension.LATENCY, Dimension.GRAPH): (0.4, "a different path does different work"),
    (Dimension.LATENCY, Dimension.CONTEXT): (0.3, "retrieval and assembly take time"),
}


def explain(differences: Sequence[Difference]) -> list[Explanation]:
    """Propose causes for observed outcome changes. Hypotheses, not findings."""
    changed = {d.dimension for d in differences}
    causes = [d for d in _INPUT_DIMENSIONS if d in changed]
    outcomes = [d for d in _OUTCOME_DIMENSIONS if d in changed]
    explanations: list[Explanation] = []
    for outcome in outcomes:
        candidates = [
            Explanation(
                observed=outcome,
                cause=cause,
                confidence=_CONFIDENCE[(outcome, cause)][0],
                rationale=_CONFIDENCE[(outcome, cause)][1],
            )
            for cause in causes
            if (outcome, cause) in _CONFIDENCE
        ]
        if not candidates:
            explanations.append(
                Explanation(
                    observed=outcome,
                    cause=None,
                    confidence=0.2,
                    rationale=(
                        "no input dimension changed; suspect provider-side drift, "
                        "external data, or model nondeterminism"
                    ),
                )
            )
            continue
        explanations.extend(sorted(candidates, key=lambda e: -e.confidence))
    return explanations


def diff_runs(
    a: RecordedRun, b: RecordedRun, *, dimensions: Sequence[Dimension] | None = None
) -> RunDiff:
    """Compare two recorded runs across the spec §28 dimensions."""
    profile_a = _profile(a)
    profile_b = _profile(b)
    wanted = list(dimensions) if dimensions is not None else list(Dimension)
    differences: list[Difference] = []
    for dimension in wanted:
        differences.extend(_compare(dimension, profile_a[dimension], profile_b[dimension]))
    return RunDiff(
        run_a=a.id,
        run_b=b.id,
        differences=differences,
        explanations=explain(differences),
    )


def diff(
    run_a: str | RecordedRun,
    run_b: str | RecordedRun,
    *,
    store: Any = None,
    dimensions: Sequence[Dimension] | None = None,
) -> RunDiff:
    """Compare two runs by id (or already-loaded recordings)."""
    left = run_a if isinstance(run_a, RecordedRun) else RecordedRun.load(run_a, store=store)
    right = run_b if isinstance(run_b, RecordedRun) else RecordedRun.load(run_b, store=store)
    return diff_runs(left, right, dimensions=dimensions)
