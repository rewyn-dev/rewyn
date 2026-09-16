"""AI dependency tracking, behavior manifests and drift (spec §34, §35, §36).

Ordinary software has a dependency manifest; AI systems mostly do not, which
is why "nothing changed but the answers got worse" is so hard to debug. This
module gives a run the same treatment a lockfile gives a build.

:class:`DependencyGraph` renders what one run actually used::

    Agent research-agent (v2)
    ├── model: fake:fake-1
    ├── skill: finance (v3)
    ├── tool: search (v1)
    └── retriever: docs (v7)

:class:`BehaviorManifest` rolls one or more runs up into a release-level
document -- models, prompts, skills, MCP servers, tools, retrievers, memory
and guardrails, each with its version and content fingerprint. Comparing two
manifests with :func:`detect_drift` answers the §35 question directly: what
changed underneath an application whose own code did not?
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.run import DependencyRef, RunManifest
from rewyn.core.schema import fingerprint, short_fingerprint
from rewyn.core.types import JSONObject, RewynError, utcnow

MANIFEST_SCHEMA_VERSION = "1"

# Dependency kind -> the manifest section it belongs to (spec §36 field names).
SECTIONS: dict[str, str] = {
    "model": "models",
    "prompt": "prompts",
    "skill": "skills",
    "mcp_server": "mcp_servers",
    "tool": "tools",
    "retriever": "retrievers",
    "memory": "memory",
    "guardrail": "guardrails",
    "agent": "agents",
    "graph": "graphs",
    "context": "contexts",
    "evaluator": "evaluators",
    "dataset": "datasets",
    "sandbox": "sandboxes",
    "embedding_model": "embedding_models",
    "reranker": "rerankers",
    "external_api": "external_apis",
}

_ROOT_KINDS = ("agent", "graph")


class ManifestError(RewynError):
    """A manifest could not be built, read or written."""


class DependencyGraph(BaseModel):
    """Every dependency a run used, and how they hang off the root (spec §34)."""

    model_config = ConfigDict(extra="forbid")

    root: str
    run_ids: list[str] = Field(default_factory=list)
    nodes: list[DependencyRef] = Field(default_factory=list)

    def of_kind(self, kind: str) -> list[DependencyRef]:
        return [n for n in self.nodes if n.kind == kind]

    def fingerprint(self) -> str:
        return fingerprint([n.model_dump(mode="json") for n in sorted(self.nodes, key=_sort_key)])

    def render(self) -> str:
        """The tree from spec §34."""
        children = [n for n in self.nodes if f"{n.kind}:{n.name}" != self.root]
        children.sort(key=_sort_key)
        lines = [self.root]
        for position, node in enumerate(children):
            elbow = "└──" if position == len(children) - 1 else "├──"
            version = "" if node.version == "unversioned" else f" (v{node.version})"
            lines.append(f"{elbow} {node.kind}: {node.name}{version}")
        return "\n".join(lines)

    @classmethod
    def from_manifests(cls, manifests: Sequence[RunManifest]) -> DependencyGraph:
        merged: dict[str, DependencyRef] = {}
        for manifest in manifests:
            for dependency in manifest.dependencies:
                merged[dependency.key] = dependency
        root = ""
        for kind in _ROOT_KINDS:
            found = next((d for d in merged.values() if d.kind == kind), None)
            if found is not None:
                version = "" if found.version == "unversioned" else f" (v{found.version})"
                root = f"{found.kind.title()} {found.name}{version}"
                break
        if not root:
            root = manifests[0].name if manifests else "run"
        return cls(
            root=root,
            run_ids=[m.id for m in manifests],
            nodes=sorted(merged.values(), key=_sort_key),
        )

    @classmethod
    def from_runs(cls, runs: Iterable[Any], *, store: Any = None) -> DependencyGraph:
        """Build from run ids, ``Run`` objects or ``RecordedRun`` objects."""
        return cls.from_manifests([_manifest_of(run, store) for run in runs])


def _sort_key(node: DependencyRef) -> tuple[str, str]:
    return (node.kind, node.name)


def _manifest_of(run: Any, store: Any = None) -> RunManifest:
    if isinstance(run, RunManifest):
        return run
    if isinstance(run, str):
        from rewyn.storage.local import LocalStore

        resolved = store or LocalStore()
        return resolved.read_manifest(resolved.resolve_run_id(run))
    manifest = getattr(run, "manifest", None)
    if isinstance(manifest, RunManifest):
        return manifest
    raise ManifestError(f"cannot read a run manifest from {type(run).__name__}")


class BehaviorManifest(BaseModel):
    """The AI equivalent of a dependency manifest for one release (spec §36)."""

    model_config = ConfigDict(extra="forbid")

    application: str
    version: str = "0.0.0"
    schema_version: str = MANIFEST_SCHEMA_VERSION
    sdk_version: str = "0.0.0"
    generated_at: datetime = Field(default_factory=utcnow)
    run_ids: list[str] = Field(default_factory=list)
    models: list[DependencyRef] = Field(default_factory=list)
    prompts: list[DependencyRef] = Field(default_factory=list)
    skills: list[DependencyRef] = Field(default_factory=list)
    mcp_servers: list[DependencyRef] = Field(default_factory=list)
    tools: list[DependencyRef] = Field(default_factory=list)
    retrievers: list[DependencyRef] = Field(default_factory=list)
    memory: list[DependencyRef] = Field(default_factory=list)
    guardrails: list[DependencyRef] = Field(default_factory=list)
    agents: list[DependencyRef] = Field(default_factory=list)
    graphs: list[DependencyRef] = Field(default_factory=list)
    contexts: list[DependencyRef] = Field(default_factory=list)
    evaluators: list[DependencyRef] = Field(default_factory=list)
    datasets: list[DependencyRef] = Field(default_factory=list)
    sandboxes: list[DependencyRef] = Field(default_factory=list)
    embedding_models: list[DependencyRef] = Field(default_factory=list)
    rerankers: list[DependencyRef] = Field(default_factory=list)
    external_apis: list[DependencyRef] = Field(default_factory=list)
    metadata: JSONObject = Field(default_factory=dict)

    # Access -------------------------------------------------------------------
    @property
    def sections(self) -> dict[str, list[DependencyRef]]:
        return {name: list(getattr(self, name)) for name in sorted(set(SECTIONS.values()))}

    def entries(self) -> list[DependencyRef]:
        return [entry for section in self.sections.values() for entry in section]

    def get(self, kind: str, name: str) -> DependencyRef | None:
        section = getattr(self, SECTIONS.get(kind, ""), [])
        return next((e for e in section if e.name == name), None)

    def fingerprint(self) -> str:
        entries = [e.model_dump(mode="json") for e in sorted(self.entries(), key=_sort_key)]
        return fingerprint(
            {"application": self.application, "version": self.version, "entries": entries}
        )

    def render(self) -> str:
        digest = short_fingerprint(self.entries())
        lines = [f"{self.application} {self.version}", f"manifest {digest}"]
        for name, entries in self.sections.items():
            if not entries:
                continue
            lines.append(f"\n{name}:")
            for entry in sorted(entries, key=_sort_key):
                version = "" if entry.version == "unversioned" else f" v{entry.version}"
                digest = f"  {entry.fingerprint[:19]}" if entry.fingerprint else ""
                lines.append(f"  - {entry.name}{version}{digest}")
        return "\n".join(lines)

    # Building -----------------------------------------------------------------
    @classmethod
    def from_runs(
        cls,
        application: str,
        runs: Iterable[Any],
        *,
        version: str = "0.0.0",
        store: Any = None,
        **metadata: Any,
    ) -> BehaviorManifest:
        """Roll one or more recorded runs up into a release manifest."""
        from rewyn import __version__

        collected: dict[str, DependencyRef] = {}
        run_ids: list[str] = []
        for run in runs:
            recorded = _recorded(run, store)
            run_ids.append(recorded.manifest.id)
            for dependency in recorded.manifest.dependencies:
                collected[dependency.key] = dependency
            for dependency in _derived_dependencies(recorded):
                collected.setdefault(dependency.key, dependency)
        manifest = cls(
            application=application,
            version=version,
            sdk_version=__version__,
            run_ids=run_ids,
            metadata=metadata,
        )
        for dependency in collected.values():
            section = SECTIONS.get(dependency.kind)
            if section is None:
                continue
            getattr(manifest, section).append(dependency)
        for section in set(SECTIONS.values()):
            getattr(manifest, section).sort(key=_sort_key)
        return manifest

    # Storage ------------------------------------------------------------------
    @staticmethod
    def directory(home: Path | None = None) -> Path:
        from rewyn.core.settings import get_settings

        return (home or get_settings().home) / "manifests"

    def save(self, *, home: Path | None = None, path: Path | None = None) -> Path:
        from rewyn.security.redaction import default_redactor
        from rewyn.storage.local import atomic_write_text

        default = BehaviorManifest.directory(home) / f"{self.application}-{self.version}.json"
        target = path or default
        payload = default_redactor().redact(self.model_dump(mode="json"))
        atomic_write_text(target, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return target

    @classmethod
    def load(cls, source: str | Path, *, home: Path | None = None) -> BehaviorManifest:
        path = Path(source)
        if path.suffix != ".json":
            path = BehaviorManifest.directory(home) / f"{source}.json"
        if not path.exists():
            raise ManifestError(f"behavior manifest not found at {path}")
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def _recorded(run: Any, store: Any = None) -> Any:
    from rewyn.replay.recorder import RecordedRun

    if isinstance(run, RecordedRun):
        return run
    if isinstance(run, RunManifest):
        # A manifest already carries the dependencies; reading its events back
        # would only add the ones derived from them, and a caller holding a
        # manifest has usually done that reading already.
        return RecordedRun(run, [])
    if isinstance(run, str):
        return RecordedRun.load(run, store=store)
    if hasattr(run, "manifest") and hasattr(run, "events"):
        return RecordedRun.from_run(run)
    raise ManifestError(f"cannot build a manifest from {type(run).__name__}")


def _derived_dependencies(recorded: Any) -> list[DependencyRef]:
    """Dependencies that are visible in events but never registered as refs."""
    from rewyn.core.event import EventType

    derived: list[DependencyRef] = []
    seen: set[str] = set()
    for event in recorded.events_of(EventType.GUARDRAIL_TRIGGERED, EventType.GUARDRAIL_PASSED):
        name = str(event.payload.get("guardrail") or "")
        if name and name not in seen:
            seen.add(name)
            derived.append(
                DependencyRef(
                    kind="guardrail",
                    name=name,
                    metadata={"stage": event.payload.get("stage")},
                )
            )
    for call in recorded.model_calls[:1]:
        system = "".join(m.text for m in call.messages if m.role.value == "system")
        if system:
            derived.append(
                DependencyRef(
                    kind="prompt",
                    name="system",
                    fingerprint=fingerprint(system),
                    metadata={"characters": len(system)},
                )
            )
    return derived


# Drift ------------------------------------------------------------------------
class DriftKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    VERSION_CHANGED = "version_changed"
    CONTENT_CHANGED = "content_changed"
    UNEXPLAINED = "unexplained"


_CAUSES: dict[str, str] = {
    "model": "model provider update",
    "prompt": "prompt changed",
    "skill": "skill changed",
    "mcp_server": "MCP server changed",
    "retriever": "retrieval changed",
    "memory": "memory changed",
    "tool": "tool behaviour changed",
    "context": "context configuration changed",
    "guardrail": "guardrail policy changed",
    "agent": "agent configuration changed",
    "graph": "graph topology changed",
}


class DriftFinding(BaseModel):
    """One dependency that moved between two manifests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: DriftKind
    dependency_kind: str
    name: str
    before: str | None = None
    after: str | None = None
    likely_cause: str = ""

    def describe(self) -> str:
        label = f"{self.dependency_kind}:{self.name}"
        if self.kind is DriftKind.ADDED:
            return f"{label} added ({self.after})"
        if self.kind is DriftKind.REMOVED:
            return f"{label} removed (was {self.before})"
        if self.kind is DriftKind.UNEXPLAINED:
            return f"{label}: {self.likely_cause}"
        return f"{label} {self.before} -> {self.after} ({self.likely_cause})"


class DriftReport(BaseModel):
    """What moved between a baseline manifest and the current one (spec §35)."""

    model_config = ConfigDict(extra="forbid")

    application: str
    baseline_version: str
    current_version: str
    findings: list[DriftFinding] = Field(default_factory=list)
    behavior_changed: bool | None = None
    """Set when two runs were compared: did the observable output change?"""

    @property
    def drifted(self) -> bool:
        return bool(self.findings)

    @property
    def silent_drift(self) -> bool:
        """Behaviour changed while every declared dependency stayed identical.

        This is the case spec §35 cares about most: the application did not
        change, so the cause is outside the manifest -- a provider update,
        external data, or model nondeterminism.
        """
        return self.behavior_changed is True and not any(
            f.kind is not DriftKind.UNEXPLAINED for f in self.findings
        )

    def of_kind(self, dependency_kind: str) -> list[DriftFinding]:
        return [f for f in self.findings if f.dependency_kind == dependency_kind]

    def render(self) -> str:
        head = (
            f"{self.application}: {self.baseline_version} -> {self.current_version}"
            if self.baseline_version != self.current_version
            else f"{self.application} {self.current_version}"
        )
        if not self.findings:
            return f"{head}\nno dependency drift"
        return "\n".join([head, *(f"  {f.describe()}" for f in self.findings)])


def detect_drift(baseline: BehaviorManifest, current: BehaviorManifest) -> DriftReport:
    """Compare two behavior manifests dependency by dependency."""
    before = {e.key: e for e in baseline.entries()}
    after = {e.key: e for e in current.entries()}
    findings: list[DriftFinding] = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old is None and new is not None:
            findings.append(
                DriftFinding(
                    kind=DriftKind.ADDED,
                    dependency_kind=new.kind,
                    name=new.name,
                    after=_identity(new),
                    likely_cause=_CAUSES.get(new.kind, "dependency added"),
                )
            )
        elif new is None and old is not None:
            findings.append(
                DriftFinding(
                    kind=DriftKind.REMOVED,
                    dependency_kind=old.kind,
                    name=old.name,
                    before=_identity(old),
                    likely_cause=_CAUSES.get(old.kind, "dependency removed"),
                )
            )
        elif old is not None and new is not None and _identity(old) != _identity(new):
            kind = (
                DriftKind.VERSION_CHANGED
                if old.version != new.version
                else DriftKind.CONTENT_CHANGED
            )
            findings.append(
                DriftFinding(
                    kind=kind,
                    dependency_kind=new.kind,
                    name=new.name,
                    before=_identity(old),
                    after=_identity(new),
                    likely_cause=_CAUSES.get(new.kind, "dependency changed"),
                )
            )
    return DriftReport(
        application=current.application,
        baseline_version=baseline.version,
        current_version=current.version,
        findings=findings,
    )


def _identity(entry: DependencyRef) -> str:
    return entry.fingerprint or f"v{entry.version}"


def detect_run_drift(baseline_run: Any, current_run: Any, *, store: Any = None) -> DriftReport:
    """Compare two runs of the same application and explain any behaviour change.

    When the output changed but no dependency did, the report says so
    explicitly rather than inventing a cause.
    """
    left = _recorded(baseline_run, store)
    right = _recorded(current_run, store)
    application = right.manifest.name
    baseline = BehaviorManifest.from_runs(application, [left], version=left.manifest.id)
    current = BehaviorManifest.from_runs(application, [right], version=right.manifest.id)
    report = detect_drift(baseline, current)
    report.behavior_changed = left.manifest.output != right.manifest.output
    if report.behavior_changed and not report.findings:
        report.findings.append(
            DriftFinding(
                kind=DriftKind.UNEXPLAINED,
                dependency_kind="output",
                name="output",
                before=str(left.manifest.output)[:120],
                after=str(right.manifest.output)[:120],
                likely_cause=(
                    "behaviour changed with every declared dependency identical; "
                    "suspect a provider update, changed external data, or model nondeterminism"
                ),
            )
        )
    return report
