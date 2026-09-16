"""The console wire format (UI spec §52).

One contract, two implementations. These models are the only shape the UI
ever sees: the local server builds them from ``.rewyn/`` and the cloud
service builds them from PostgreSQL, so a screen written against one surface
works unchanged against the other. Nothing here exposes storage -- UI §52 is
explicit that database details stay behind the API.

Run-scoped views are *projections* (see :mod:`rewyn.ui.projections`): small
documents derived from a run's manifest and event log, fetched one tab at a
time so opening a run never loads its whole payload (UI §50).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.types import JSONObject

CONSOLE_API_VERSION = "v1"

CONSOLE_PREFIX = f"/console/{CONSOLE_API_VERSION}"
"""Where the console API lives on both surfaces.

The cloud already serves the SDK's ingest and sync API at ``/v1``; the console
is a second API with its own audience and its own version, so it gets its own
base path and the frontend addresses one path everywhere (UI §52).
"""

Surface = Literal["local", "cloud"]


class ConsoleModel(BaseModel):
    """Base for every console document: strict, JSON-only."""

    model_config = ConfigDict(extra="forbid")


class ProblemDetail(ConsoleModel):
    """An error the user can act on (UI §48).

    "Error 500" is the counter-example the spec gives. Every failure carries a
    title, what actually happened, and -- where one exists -- the label and
    target of the action that recovers from it.
    """

    error: str
    detail: str = ""
    action: str | None = None
    href: str | None = None


# Capabilities -----------------------------------------------------------------
class ProjectLocation(ConsoleModel):
    """Where "here" is, and how to be somewhere else (UI §41).

    A project is not something the console creates. Locally it is a name on
    a directory; in the cloud it is a tenant somebody provisioned. Saying so
    is the difference between a design fact and an apparently missing button.
    """

    surface: Surface
    project: str
    home: str | None = None
    """The directory being read, on the local surface."""

    endpoint: str | None = None
    """The service being read, on the cloud surface."""

    how_to_change: str = ""
    """One sentence: how to point this console somewhere else."""


class ConsoleCounts(ConsoleModel):
    """How much of the product is reachable yet.

    Screens are never hidden -- the navigation is the map of the product
    (UI §4) -- but a screen that cannot answer its question yet should say
    what would let it, and that needs numbers the shell can see (UI §47).
    """

    runs: int = 0
    agents: int = 0
    datasets: int = 0
    reports: int = 0
    failures: int = 0
    versions: int = 0
    """The most versions any one agent has. Drift and releases need two."""


class Capabilities(ConsoleModel):
    """What this surface can do, so the UI hides what it cannot serve (UI §45)."""

    surface: Surface
    api_version: str = CONSOLE_API_VERSION
    sdk_version: str
    project: str
    project_id: str | None = None
    role: str = "owner"
    features: list[str] = Field(default_factory=list)
    environments: list[str] = Field(default_factory=list)
    location: ProjectLocation | None = None
    counts: ConsoleCounts = Field(default_factory=ConsoleCounts)
    demo_loaded: bool = False


class OnboardingStep(ConsoleModel):
    """One step of the path from nothing to a useful console (UI §47)."""

    key: str
    title: str
    detail: str
    done: bool = False
    code: str = ""
    href: str | None = None
    action: str = ""


class UseCase(ConsoleModel):
    """A job the tool is for, and whether you can do it here yet (UI §59)."""

    key: str
    title: str
    question: str
    detail: str
    href: str
    ready: bool = False
    needs: str = ""


class Onboarding(ConsoleModel):
    """What to do first, for a console with nothing in it yet (UI §47)."""

    location: ProjectLocation
    counts: ConsoleCounts = Field(default_factory=ConsoleCounts)
    empty: bool = True
    demo_loaded: bool = False
    can_load_demo: bool = False
    summary: str = ""
    steps: list[OnboardingStep] = Field(default_factory=list)
    jobs: list[UseCase] = Field(default_factory=list)


# Runs -------------------------------------------------------------------------
class RunSummary(ConsoleModel):
    """One row of the runs table (UI §7)."""

    id: str
    name: str
    status: str
    project: str = "default"
    environment: str = "development"
    agent: str | None = None
    user: str | None = None
    model: str | None = None
    session_id: str | None = None
    parent_run_id: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float = 0.0
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    event_count: int = 0
    error: str | None = None
    tags: list[str] = Field(default_factory=list)
    eval_score: float | None = None


class RunPage(ConsoleModel):
    runs: list[RunSummary] = Field(default_factory=list)
    total: int = 0
    estimated: bool = False
    """True when ``total`` is a lower bound because the match set is very large."""

    next_cursor: str | None = None


class DependencyView(ConsoleModel):
    kind: str
    name: str
    version: str = "unversioned"
    fingerprint: str | None = None
    metadata: JSONObject = Field(default_factory=dict)


class PanelCounts(ConsoleModel):
    """How much each run-detail tab holds, so empty tabs can be marked (UI §8)."""

    timeline: int = 0
    graph: int = 0
    context: int = 0
    model: int = 0
    prompt: int = 0
    memory: int = 0
    tools: int = 0
    mcp: int = 0
    skills: int = 0
    guardrails: int = 0
    approvals: int = 0


class CostBreakdown(ConsoleModel):
    """The §33 categories, which are the ones the SDK actually accounts for."""

    model: float = 0.0
    tool: float = 0.0
    embedding: float = 0.0
    retrieval: float = 0.0
    sandbox: float = 0.0
    total: float = 0.0
    currency: str = "USD"


class RunDetail(ConsoleModel):
    """The run header (UI §8). Panels are fetched separately (UI §50)."""

    run: RunSummary
    input: Any = None
    output: Any = None
    error: str | None = None
    traceback: str | None = None
    dependencies: list[DependencyView] = Field(default_factory=list)
    panels: PanelCounts = Field(default_factory=PanelCounts)
    cost: CostBreakdown = Field(default_factory=CostBreakdown)
    metadata: JSONObject = Field(default_factory=dict)


# Timeline ---------------------------------------------------------------------
class TimelineEntry(ConsoleModel):
    """One expandable row of the run timeline (UI §9)."""

    seq: int
    type: str
    timestamp: datetime
    offset_ms: float
    span_id: str | None = None
    parent_span_id: str | None = None
    depth: int = 0
    label: str
    detail: str = ""
    status: Literal["ok", "error", "warning", "running"] = "ok"
    duration_ms: float | None = None
    payload: JSONObject = Field(default_factory=dict)
    """Trimmed for the list; the full payload comes from the events endpoint."""


class TimelineView(ConsoleModel):
    run_id: str
    started_at: datetime
    entries: list[TimelineEntry] = Field(default_factory=list)
    total: int = 0
    next_seq: int | None = None


class EventPage(ConsoleModel):
    """Raw events, paginated by ``seq`` (UI §50)."""

    run_id: str
    events: list[JSONObject] = Field(default_factory=list)
    total: int = 0
    next_seq: int | None = None


# Execution graph --------------------------------------------------------------
class GraphNodeView(ConsoleModel):
    id: str
    label: str
    kind: str
    status: Literal["ok", "error", "running", "skipped"] = "ok"
    started_at: datetime | None = None
    duration_ms: float | None = None
    seq: int | None = None
    parent: str | None = None
    detail: JSONObject = Field(default_factory=dict)


class GraphEdgeView(ConsoleModel):
    source: str
    target: str
    kind: Literal["flow", "spawn", "handoff"] = "flow"
    label: str = ""


class GraphView(ConsoleModel):
    """A DAG for graph runs, a tree for multi-agent runs (UI §10)."""

    run_id: str
    shape: Literal["graph", "agents", "linear"] = "linear"
    root: str | None = None
    nodes: list[GraphNodeView] = Field(default_factory=list)
    edges: list[GraphEdgeView] = Field(default_factory=list)


# Context ----------------------------------------------------------------------
class ContextItemView(ConsoleModel):
    """One context item with everything UI §11 asks to display."""

    id: str
    kind: str
    title: str | None = None
    source: str | None = None
    record: str | None = None
    version: str | None = None
    uri: str | None = None
    retrieved_at: datetime | None = None
    hash: str | None = None
    tokens: int = 0
    relevance: float = 0.0
    authority: float = 0.5
    trust_level: str = "internal"
    sensitivity: str = "internal"
    verified: bool | None = None
    included: bool = True
    excluded_reason: str | None = None
    redacted: bool = False
    redaction_reason: str | None = None


class ContextAssembly(ConsoleModel):
    seq: int
    context: str
    version: str = "1"
    query: str | None = None
    fingerprint: str | None = None
    budget: int = 0
    used: int = 0
    by_kind: dict[str, int] = Field(default_factory=dict)
    items: list[ContextItemView] = Field(default_factory=list)
    dropped_untrusted: list[str] = Field(default_factory=list)
    stale: list[str] = Field(default_factory=list)
    assembled_in_ms: float = 0.0


class ContextView(ConsoleModel):
    """The context inspector (UI §11) and its composition (UI §12)."""

    run_id: str
    assemblies: list[ContextAssembly] = Field(default_factory=list)
    retrievals: list[JSONObject] = Field(default_factory=list)


# Model, prompt ----------------------------------------------------------------
class ModelCallView(ConsoleModel):
    """One model call (UI §14)."""

    index: int
    seq: int
    provider: str = ""
    model: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    seed: int | None = None
    stop: list[str] = Field(default_factory=list)
    reasoning: JSONObject = Field(default_factory=dict)
    output_schema: JSONObject | None = None
    tools: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0
    cost_source: str = "unknown"
    latency_ms: float = 0.0
    finish_reason: str | None = None
    request_fingerprint: str | None = None
    error: str | None = None
    text: str = ""


class ModelView(ConsoleModel):
    run_id: str
    calls: list[ModelCallView] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    total_cost: float = 0.0
    total_latency_ms: float = 0.0


class PromptMessageView(ConsoleModel):
    role: str
    name: str | None = None
    text: str
    hash: str
    tokens: int = 0


class PromptCallView(ConsoleModel):
    """The exact prompt one call used, split by role (UI §15)."""

    index: int
    seq: int
    model: str = ""
    fingerprint: str | None = None
    system: list[PromptMessageView] = Field(default_factory=list)
    developer: list[PromptMessageView] = Field(default_factory=list)
    user: list[PromptMessageView] = Field(default_factory=list)
    assistant: list[PromptMessageView] = Field(default_factory=list)
    tool: list[PromptMessageView] = Field(default_factory=list)
    tool_instructions: list[PromptMessageView] = Field(default_factory=list)
    skill_instructions: list[PromptMessageView] = Field(default_factory=list)


class PromptView(ConsoleModel):
    run_id: str
    calls: list[PromptCallView] = Field(default_factory=list)


# Memory -----------------------------------------------------------------------
class MemoryOpView(ConsoleModel):
    """One memory read or write (UI §16)."""

    seq: int
    operation: Literal["read", "write"]
    memory: str = ""
    kind: str = ""
    id: str | None = None
    content: str = ""
    query: str | None = None
    score: float | None = None
    confidence: float | None = None
    importance: float | None = None
    provider: str | None = None
    created_at: datetime | None = None
    timestamp: datetime


class MemoryView(ConsoleModel):
    run_id: str
    operations: list[MemoryOpView] = Field(default_factory=list)


# Tools ------------------------------------------------------------------------
class ToolCallView(ConsoleModel):
    """One tool call (UI §17)."""

    index: int
    seq: int
    tool_call_id: str
    name: str
    version: str | None = None
    fingerprint: str | None = None
    server: str | None = None
    """Set when the tool came from an MCP server."""

    arguments: JSONObject = Field(default_factory=dict)
    result: Any = None
    status: Literal["success", "error", "denied"] = "success"
    duration_ms: float = 0.0
    replayable: bool = True
    """False when the tool declared side effects, so the UI hides `Replay tool call`."""


class ToolView(ConsoleModel):
    run_id: str
    calls: list[ToolCallView] = Field(default_factory=list)


# MCP --------------------------------------------------------------------------
class McpToolView(ConsoleModel):
    name: str
    fingerprint: str | None = None
    risk_level: str | None = None
    calls: int = 0


class McpServerView(ConsoleModel):
    """One MCP server as used by this run (UI §18)."""

    server: str
    version: str = "unversioned"
    transport: str | None = None
    fingerprint: str | None = None
    connected_at: datetime | None = None
    latency_ms: float = 0.0
    tools: list[McpToolView] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    changed_since_previous_run: bool = False
    previous_version: str | None = None


class McpView(ConsoleModel):
    run_id: str
    servers: list[McpServerView] = Field(default_factory=list)


# Skills -----------------------------------------------------------------------
class SkillUseView(ConsoleModel):
    """A skill and how far it got: discovered, loaded, used (UI §19)."""

    skill: str
    version: str = "unversioned"
    fingerprint: str | None = None
    state: Literal["discovered", "loaded", "used"] = "discovered"
    description: str = ""
    resources: list[str] = Field(default_factory=list)
    scripts: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    activations: int = 0


class SkillsView(ConsoleModel):
    run_id: str
    skills: list[SkillUseView] = Field(default_factory=list)


# Guardrails and approvals -----------------------------------------------------
class GuardrailView(ConsoleModel):
    seq: int
    guardrail: str = ""
    stage: str = ""
    triggered: bool = False
    action: str | None = None
    detail: JSONObject = Field(default_factory=dict)


class ApprovalView(ConsoleModel):
    """A human decision, which UI §35 requires to be part of the run."""

    request_id: str
    action: str
    risk: str | None = None
    details: JSONObject = Field(default_factory=dict)
    requested_at: datetime
    decided_at: datetime | None = None
    decision: Literal["pending", "approved", "rejected"] = "pending"
    by: str | None = None
    reason: str | None = None
    run_id: str | None = None


class ScoreView(ConsoleModel):
    """One evaluator's verdict on one run (UI §7 filter, UI §27)."""

    subject_run_id: str
    evaluator: str
    version: str = "1"
    value: float = 0.0
    passed: bool = False
    label: str | None = None
    reason: str = ""
    threshold: float | None = None
    scored_at: datetime
    scored_in_run_id: str


# Replay and compare -----------------------------------------------------------
Setting = Literal["original", "new", "recorded", "live"]

REPLAY_COMPONENTS: tuple[str, ...] = (
    "model",
    "prompt",
    "context",
    "memory",
    "skills",
    "tools",
    "mcp",
    "temperature",
    "system",
)
"""The nine controls UI §21 lists, in the order it lists them."""


class ReplayComponent(ConsoleModel):
    """One row of the replay control matrix (UI §21)."""

    component: str
    mode: Setting = "original"
    value: str | None = None
    """The replacement, when the mode is ``new``: a model id, a temperature."""


class ReplayRequest(ConsoleModel):
    components: list[ReplayComponent] = Field(default_factory=list)


class ReplayComponentPlan(ConsoleModel):
    """What a control will actually do, in plain language (UI §21)."""

    component: str
    mode: Setting
    value: str | None = None
    supported: bool = True
    effect: str = ""


class ReplayPlan(ConsoleModel):
    """The replay that will run, before it runs."""

    run_id: str
    mode: Literal["reconstruct", "prompt", "execute"] = "reconstruct"
    components: list[ReplayComponentPlan] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PromptComparison(ConsoleModel):
    """One recorded request, answered twice (UI §20)."""

    index: int
    original_model: str
    replay_model: str
    original_text: str = ""
    replay_text: str = ""
    original_tool_calls: list[str] = Field(default_factory=list)
    replay_tool_calls: list[str] = Field(default_factory=list)
    original_cost: float = 0.0
    replay_cost: float = 0.0
    original_latency_ms: float = 0.0
    replay_latency_ms: float = 0.0
    changed: bool = False
    error: str | None = None


class MismatchView(ConsoleModel):
    """A replayed call that did not line up with the recording."""

    kind: str
    reason: str
    index: int
    expected: str | None = None
    actual: str | None = None
    detail: str = ""


class ReplayView(ConsoleModel):
    """A replay: queued, running, or finished (UI §20)."""

    id: str
    status: Literal["queued", "running", "succeeded", "failed"] = "queued"
    plan: ReplayPlan
    original_run_id: str
    replay_run_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    identical: bool | None = None
    faithful: bool | None = None
    original_output: Any = None
    output: Any = None
    prompts: list[PromptComparison] = Field(default_factory=list)
    mismatches: list[MismatchView] = Field(default_factory=list)
    substitutions: int = 0
    original_cost: float = 0.0
    cost: float = 0.0
    duration_ms: float = 0.0
    problem: ProblemDetail | None = None


class DifferenceView(ConsoleModel):
    """One observed change between two runs. A fact, never a hypothesis (UI §23)."""

    dimension: str
    field: str
    kind: str
    before: Any = None
    after: Any = None
    delta: float | None = None
    description: str = ""


class ExplanationView(ConsoleModel):
    """A hypothesis linking an input change to an outcome change (UI §23)."""

    observed: str
    cause: str | None = None
    confidence: float = 0.0
    rationale: str
    description: str = ""


class DimensionSummary(ConsoleModel):
    """One row of the WHAT CHANGED? table (UI §22)."""

    dimension: str
    changed: bool = False
    differences: int = 0


class DiffView(ConsoleModel):
    """Two runs compared (UI §13, §22, §23)."""

    run_a: str
    run_b: str
    summary_a: RunSummary
    summary_b: RunSummary
    identical: bool = True
    dimensions: list[DimensionSummary] = Field(default_factory=list)
    differences: list[DifferenceView] = Field(default_factory=list)
    explanations: list[ExplanationView] = Field(default_factory=list)
    output_changed: bool = False
    cost_delta_percent: float | None = None
    latency_delta_percent: float | None = None
    quality_delta: float | None = None


# Datasets ---------------------------------------------------------------------
class DatasetCaseView(ConsoleModel):
    """One case of a regression dataset (UI §25)."""

    id: str
    input: Any = None
    expected: Any = None
    tags: list[str] = Field(default_factory=list)
    source_run_id: str | None = None
    evaluator: str | None = None
    severity: str | None = None
    created_at: datetime
    last_result: str | None = None
    status: str | None = None


class DatasetSummary(ConsoleModel):
    name: str
    version: str = "1"
    description: str = ""
    cases: int = 0
    tags: list[str] = Field(default_factory=list)
    updated_at: datetime
    passed: int | None = None
    failed: int | None = None
    latest_run: str | None = None


class DatasetDetail(DatasetSummary):
    items: list[DatasetCaseView] = Field(default_factory=list)


class SaveAsTestRequest(ConsoleModel):
    """The Save as Test dialog (UI §24)."""

    dataset: str = Field(min_length=1, max_length=200)
    expected: Any = None
    evaluator: str | None = None
    severity: Literal["critical", "high", "medium", "low"] | None = None
    tags: list[str] = Field(default_factory=list)


# BUILD registries, agents and quality (UI §26-§30) -----------------------------
class RegistryVersion(ConsoleModel):
    """One version of one dependency, and how much it was used."""

    version: str = "unversioned"
    runs: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    fingerprint: str | None = None


class RegistryEntry(ConsoleModel):
    """One row of a BUILD page: a model, prompt, skill, MCP server or tool."""

    kind: str
    name: str
    version: str = "unversioned"
    versions: int = 1
    runs: int = 0
    last_used_at: datetime | None = None


class RegistryDetail(ConsoleModel):
    kind: str
    name: str
    runs: int = 0
    versions: list[RegistryVersion] = Field(default_factory=list)
    used_by: list[str] = Field(default_factory=list)
    """Agents and graphs whose runs used this."""

    recent_runs: list[RunSummary] = Field(default_factory=list)
    metadata: JSONObject = Field(default_factory=dict)


class AgentSummary(ConsoleModel):
    """An agent as the console knows it: from what its runs used (UI §29)."""

    name: str
    kind: str = "agent"
    version: str = "unversioned"
    versions: int = 1
    environments: list[str] = Field(default_factory=list)
    runs: int = 0
    success_rate: float = 0.0
    avg_cost: float = 0.0
    avg_latency_ms: float = 0.0
    eval_score: float | None = None
    model: str | None = None
    tools: int = 0
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    memory: bool = False
    last_run_at: datetime | None = None


class DriftFindingView(ConsoleModel):
    """One dependency that moved between two versions (UI §30, §32)."""

    kind: str
    dependency_kind: str
    name: str
    before: str | None = None
    after: str | None = None
    before_version: str | None = None
    after_version: str | None = None
    likely_cause: str = ""


class ManifestDiff(ConsoleModel):
    """v17 → v18, exactly what changed (UI §30)."""

    application: str
    before_version: str
    after_version: str
    findings: list[DriftFindingView] = Field(default_factory=list)
    unchanged: int = 0


class ScoreBucket(ConsoleModel):
    lower: float
    upper: float
    count: int = 0


class EvaluatorSummary(ConsoleModel):
    """One evaluator across everything it judged (UI §27)."""

    name: str
    scores: int = 0
    mean: float = 0.0
    pass_rate: float = 0.0
    distribution: list[ScoreBucket] = Field(default_factory=list)
    last_scored_at: datetime | None = None


class EvaluationsView(ConsoleModel):
    evaluators: list[EvaluatorSummary] = Field(default_factory=list)
    scores: int = 0
    runs_scored: int = 0


class MetricSummaryView(ConsoleModel):
    name: str
    passed: int = 0
    total: int = 0
    mean: float = 0.0
    pass_rate: float = 0.0
    threshold: float | None = None
    baseline_mean: float | None = None


class GateView(ConsoleModel):
    name: str
    passed: bool
    actual: float
    limit: float
    comparison: str
    detail: str = ""


class RegressionSummary(ConsoleModel):
    """One regression run (UI §26)."""

    id: str
    dataset: str
    dataset_version: str = "1"
    target: str = ""
    created_at: datetime
    tests: int = 0
    succeeded: int = 0
    success_rate: float = 0.0
    success_delta: float | None = None
    avg_cost: float = 0.0
    avg_latency_ms: float = 0.0
    passed: bool = False
    baseline_id: str | None = None


class AgentDetail(AgentSummary):
    """The Overview tab of an agent page, plus what the other tabs need."""

    version_history: list[RegistryVersion] = Field(default_factory=list)
    dependencies: list[DependencyView] = Field(default_factory=list)
    recent_runs: list[RunSummary] = Field(default_factory=list)
    reports: list[RegressionSummary] = Field(default_factory=list)


class RegressionCaseView(ConsoleModel):
    item_id: str
    run_id: str | None = None
    passed: bool = False
    output: str = ""
    expected: Any = None
    cost: float = 0.0
    latency_ms: float = 0.0
    error: str | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    regressed: bool = False
    """True when this case passed in the baseline and fails now."""


class RegressionDetail(RegressionSummary):
    metrics: list[MetricSummaryView] = Field(default_factory=list)
    gates: list[GateView] = Field(default_factory=list)
    cases: list[RegressionCaseView] = Field(default_factory=list)
    regressions: list[RegressionCaseView] = Field(default_factory=list)
    baseline: RegressionSummary | None = None


class ExperimentRequest(ConsoleModel):
    """The NEW EXPERIMENT form (UI §26)."""

    dataset: str = Field(min_length=1, max_length=200)
    target: str = ""
    baseline: str | None = None
    evaluators: list[str] = Field(default_factory=list)
    min_success: float | None = None
    max_cost: float | None = None
    concurrency: int = 1


class ExperimentPlan(ConsoleModel):
    """How to run the experiment, and why the console cannot run it itself."""

    dataset: str
    command: str
    explanation: str
    reports_url: str = "/regression"


# Live (UI §34, §53) -----------------------------------------------------------
class LiveRun(ConsoleModel):
    """One in-flight run, as the live view sees it (UI §34)."""

    run: RunSummary
    events: int = 0
    stage: str = ""
    """The most recent thing that happened, in words."""

    context_tokens: int = 0
    cost: float = 0.0
    nodes: list[GraphNodeView] = Field(default_factory=list)
    updated_at: datetime


class LiveFrame(ConsoleModel):
    """One server-sent frame of the live view."""

    runs: list[LiveRun] = Field(default_factory=list)
    sent_at: datetime


class StreamFrame(ConsoleModel):
    """One server-sent frame of a single run's stream (UI §53)."""

    run_id: str
    entries: list[TimelineEntry] = Field(default_factory=list)
    status: str = "running"
    finished: bool = False
    sent_at: datetime


class ApprovalDecisionRequest(ConsoleModel):
    """Approve, reject, or ask for changes (UI §35)."""

    approved: bool
    by: str = "console"
    reason: str = ""
    correction: JSONObject | None = None


class PendingApprovalView(ConsoleModel):
    """A request waiting for a person (UI §35)."""

    request_id: str
    action: str
    risk: str = "medium"
    details: JSONObject = Field(default_factory=dict)
    requested_at: datetime
    run_id: str | None = None
    decision: Literal["pending", "approved", "rejected"] = "pending"
    by: str | None = None
    reason: str | None = None
    expires_at: datetime | None = None
    expired: bool = False


# Intelligence: dependencies, drift, cost, releases (UI §28, §31-§33, §40, §43)
class DependencyNode(ConsoleModel):
    """One node of the dependency map (UI §31). Every node is clickable."""

    id: str
    kind: str
    name: str
    version: str = "unversioned"
    runs: int = 0
    href: str | None = None
    """Where clicking it goes -- a BUILD page, when the console has one."""


class DependencyEdge(ConsoleModel):
    source: str
    target: str
    relation: str = "uses"


class DependencyMap(ConsoleModel):
    """What one agent depends on, and what those depend on (UI §31)."""

    root: str
    agent: str
    runs: int = 0
    nodes: list[DependencyNode] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)
    fingerprint: str = ""


class DriftCause(ConsoleModel):
    """A possible cause, with the evidence for it (UI §32)."""

    kind: str
    dependency_kind: str = ""
    name: str = ""
    before: str | None = None
    after: str | None = None
    detail: str = ""
    changed: bool = True


class DriftView(ConsoleModel):
    """Behaviour drift for one agent, evidence first (UI §32)."""

    agent: str
    baseline_runs: int = 0
    current_runs: int = 0
    baseline_from: datetime | None = None
    current_from: datetime | None = None
    expected_success: float = 0.0
    current_success: float = 0.0
    expected_cost: float = 0.0
    current_cost: float = 0.0
    drifted: bool = False
    silent: bool = False
    """Behaviour moved while every recorded dependency stayed identical."""

    causes: list[DriftCause] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)


class CostSlice(ConsoleModel):
    """One row of the cost page (UI §33)."""

    key: str
    total: float = 0.0
    runs: int = 0
    succeeded: int = 0
    per_run: float = 0.0
    per_successful_task: float | None = None
    share: float = 0.0


class CostView(ConsoleModel):
    """Cost by whichever dimension was asked for (UI §33)."""

    group_by: str
    total: float = 0.0
    runs: int = 0
    succeeded: int = 0
    per_successful_task: float | None = None
    categories: CostBreakdown = Field(default_factory=CostBreakdown)
    slices: list[CostSlice] = Field(default_factory=list)
    currency: str = "USD"


class ReleaseComponent(ConsoleModel):
    kind: str
    name: str
    before: str | None = None
    after: str | None = None


class PromotionRecord(ConsoleModel):
    """An authorized promotion: who said yes, and to what (UI §43)."""

    release_id: str
    application: str
    version: str
    environment: str = "production"
    by: str
    at: datetime
    report_id: str | None = None
    note: str = ""


class PromoteRequest(ConsoleModel):
    environment: str = "production"
    note: str = ""
    by: str | None = None


class ReleaseView(ConsoleModel):
    """One agent version, and whether it is safe to deploy (UI §43)."""

    id: str = ""
    """``<application>@<version>``: stable, so a release can be linked to."""

    application: str
    version: str
    previous_version: str | None = None
    created_at: datetime | None = None
    runs: int = 0
    changed: list[ReleaseComponent] = Field(default_factory=list)
    tests: int = 0
    passed: int = 0
    failed: int = 0
    success_rate: float | None = None
    cost_delta_percent: float | None = None
    report_id: str | None = None
    status: Literal["ready", "blocked", "unverified"] = "unverified"
    blocked_by: list[str] = Field(default_factory=list)
    promotion: str = ""
    """How this version actually reaches an environment."""

    promoted: PromotionRecord | None = None
    """Set once somebody authorized this version for an environment."""


class VariantView(ConsoleModel):
    """One arm of an experiment (UI §28)."""

    label: str
    report_id: str
    target: str = ""
    tests: int = 0
    success_rate: float = 0.0
    avg_cost: float = 0.0
    avg_latency_ms: float = 0.0
    failures: int = 0
    metrics: dict[str, float] = Field(default_factory=dict)
    winner: bool = False


class ExperimentView(ConsoleModel):
    """Control against variants, on one dataset (UI §28)."""

    dataset: str
    variants: list[VariantView] = Field(default_factory=list)
    measured: list[str] = Field(default_factory=list)


class NotificationView(ConsoleModel):
    """A meaningful AI event, not a log line (UI §40)."""

    id: str
    kind: str
    severity: Literal["info", "warning", "critical"] = "info"
    summary: str
    detail: str = ""
    at: datetime
    href: str | None = None


# Project-level documents ------------------------------------------------------
class OverviewMetric(ConsoleModel):
    label: str
    value: float
    unit: Literal["percent", "seconds", "currency", "count"] = "count"
    delta: float | None = None


class RecentChange(ConsoleModel):
    """A dependency that changed version between consecutive runs (UI §6)."""

    at: datetime
    kind: str
    name: str
    before: str | None = None
    after: str | None = None
    run_id: str
    summary: str


class Incident(ConsoleModel):
    """A cluster worth looking at, evidence first (UI §6, §36)."""

    id: str
    severity: Literal["warning", "critical"] = "warning"
    summary: str
    detail: str = ""
    run_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    affected_runs: int = 0
    likely_cause: str | None = None
    agent: str | None = None
    environment: str | None = None


class Overview(ConsoleModel):
    """ "Is my AI system healthy?" (UI §6)."""

    project: str
    environment: str | None = None
    status: Literal["healthy", "degraded", "unhealthy", "unknown"] = "unknown"
    metrics: list[OverviewMetric] = Field(default_factory=list)
    recent_changes: list[RecentChange] = Field(default_factory=list)
    incidents: list[Incident] = Field(default_factory=list)
    recent_runs: list[RunSummary] = Field(default_factory=list)


class SearchHit(ConsoleModel):
    group: str
    id: str
    label: str
    detail: str = ""
    href: str


class SearchResults(ConsoleModel):
    """Grouped in the order UI §5 prints them."""

    query: str
    groups: list[str] = Field(default_factory=list)
    hits: list[SearchHit] = Field(default_factory=list)
    total: int = 0


class SessionView(ConsoleModel):
    id: str
    runs: int = 0
    started_at: datetime
    ended_at: datetime | None = None
    agents: list[str] = Field(default_factory=list)
    user: str | None = None
    cost: float = 0.0
    failures: int = 0


class EnvironmentView(ConsoleModel):
    name: str
    runs: int = 0
    last_run_at: datetime | None = None


class FacetValue(ConsoleModel):
    value: str
    count: int = 0


class RunFacets(ConsoleModel):
    """The filter vocabulary for the runs page (UI §7)."""

    agent: list[FacetValue] = Field(default_factory=list)
    model: list[FacetValue] = Field(default_factory=list)
    user: list[FacetValue] = Field(default_factory=list)
    status: list[FacetValue] = Field(default_factory=list)
    environment: list[FacetValue] = Field(default_factory=list)
    tool: list[FacetValue] = Field(default_factory=list)
    mcp: list[FacetValue] = Field(default_factory=list)
    skill: list[FacetValue] = Field(default_factory=list)
    tag: list[FacetValue] = Field(default_factory=list)
    error: list[FacetValue] = Field(default_factory=list)


# The control room: incidents, collaboration, narrative, workspace (UI §23, §36, §37, §60) ------
IncidentStatus = Literal["open", "investigating", "mitigated", "resolved"]

INCIDENT_STAGES: tuple[str, ...] = (
    "deployment",
    "behavior_change",
    "detection",
    "investigation",
    "fix",
    "regression",
    "resolved",
)
"""The seven steps UI §36 prints, in order."""


class IncidentStage(ConsoleModel):
    """One step of the §36 timeline, and the evidence that it happened.

    A stage is ``reached`` only when something recorded says so: a dependency
    version that moved, a failing run, a regression report, a person's note.
    Stages ahead of the evidence are returned too, unreached, because the
    shape of the timeline is what makes the missing step legible.
    """

    stage: str
    reached: bool = False
    at: datetime | None = None
    summary: str = ""
    evidence: str = ""
    href: str | None = None


class IncidentComment(ConsoleModel):
    """A note left on a run, an incident or a saved view (UI §45)."""

    id: str
    subject: str
    author: str
    body: str
    created_at: datetime
    resolved: bool = False


class IncidentDetail(Incident):
    """One incident, its timeline and everyone working on it (UI §36)."""

    status: IncidentStatus = "open"
    assignee: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    cause_evidence: list[str] = Field(default_factory=list)
    timeline: list[IncidentStage] = Field(default_factory=list)
    runs: list[RunSummary] = Field(default_factory=list)
    comments: list[IncidentComment] = Field(default_factory=list)
    note: str = ""


class IncidentUpdate(ConsoleModel):
    """Assign an incident, or move it along its timeline (UI §36, §45)."""

    status: IncidentStatus | None = None
    assignee: str | None = None
    note: str | None = None


class CommentRequest(ConsoleModel):
    subject: str
    body: str
    author: str | None = None


class SavedView(ConsoleModel):
    """A filter someone found worth keeping, shared with the team (UI §45)."""

    id: str
    name: str
    screen: str
    query: str = ""
    author: str = ""
    created_at: datetime
    shared: bool = True
    description: str = ""


class SavedViewRequest(ConsoleModel):
    name: str
    screen: str
    query: str = ""
    description: str = ""
    shared: bool = True
    author: str | None = None


class NarrativeClaim(ConsoleModel):
    """One sentence of the explanation, and where it came from (UI §23).

    ``label`` is the word the console prints beside it -- Observed for a fact
    read out of the two recordings, Inference for a hypothesis about what it
    caused. ``evidence`` names the differences the claim rests on; a claim
    that cites nothing is not returned.
    """

    label: Literal["Observed", "Inference"]
    text: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = None


class NarrativeView(ConsoleModel):
    """The answer to "Explain difference" (UI §23).

    ``author`` says who wrote it. ``model`` means an explanation agent ran --
    itself a recorded Rewyn run, named by ``run_id``, so the explanation can
    be replayed and audited like anything else. ``rules`` means no model was
    available and the console composed the narrative from the diff directly.
    """

    run_a: str
    run_b: str
    author: Literal["model", "rules"] = "rules"
    model: str | None = None
    run_id: str | None = None
    summary: str = ""
    claims: list[NarrativeClaim] = Field(default_factory=list)
    grounded: bool = True
    dropped: list[str] = Field(default_factory=list)
    """Sentences the model produced that cited no evidence, and were removed."""

    note: str = ""


class WorkspaceStage(ConsoleModel):
    """One band of the §37 workspace, or one step of the §60 loop."""

    key: str
    label: str
    question: str
    ready: bool = False
    href: str
    summary: str = ""
    detail: str = ""
    count: int | None = None
    status: Literal["ok", "warn", "fail", "idle"] = "idle"


class WorkspaceComponent(ConsoleModel):
    kind: str
    name: str
    version: str = "unversioned"
    href: str | None = None


class WorkspaceView(ConsoleModel):
    """Config → run → inspect → replay → evaluate → regression, in one frame (UI §37)."""

    agent: str
    version: str = "unversioned"
    environment: str | None = None
    components: list[WorkspaceComponent] = Field(default_factory=list)
    stages: list[WorkspaceStage] = Field(default_factory=list)
    loop: list[WorkspaceStage] = Field(default_factory=list)
    latest_run: RunSummary | None = None
    recent_runs: list[RunSummary] = Field(default_factory=list)
    incidents: list[Incident] = Field(default_factory=list)
    next_step: str = ""
