"""Run projections: the console's read model (UI spec §8-§19).

A run is persisted as a manifest plus an ordered event log, and that is all
the console ever needs. This module turns that pair into the small documents
each run-detail tab renders, so a tab fetches only its own view and opening a
run never loads the whole payload (UI §50).

The same functions run on both surfaces: the local server calls them on
demand against ``.rewyn/``, the cloud calls them once at ingest and stores
the result. That is deliberate -- one implementation means the local UI and
the console cannot drift apart.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, Literal

from rewyn.core.event import Event, EventType
from rewyn.core.run import RunManifest
from rewyn.replay.recorder import RecordedRun
from rewyn.ui import schemas as s

# Sensitivity ranking, lowest first (SDK §39).
SENSITIVITY_ORDER: dict[str, int] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}

# The highest sensitivity each role may read (UI §54).
ROLE_SENSITIVITY: dict[str, str] = {
    "viewer": "internal",
    "member": "confidential",
    "admin": "restricted",
    "owner": "restricted",
}

# Human labels for the timeline (UI §9).
EVENT_LABELS: dict[EventType, str] = {
    EventType.RUN_STARTED: "Run started",
    EventType.RUN_FINISHED: "Run finished",
    EventType.RUN_FAILED: "Run failed",
    EventType.RUN_CANCELLED: "Run cancelled",
    EventType.MODEL_CALLED: "Model call",
    EventType.MODEL_RESPONSE: "Model response",
    EventType.OUTPUT_VALIDATED: "Output validated",
    EventType.CONTEXT_RETRIEVED: "Context retrieved",
    EventType.CONTEXT_ASSEMBLED: "Context built",
    EventType.MEMORY_READ: "Memory read",
    EventType.MEMORY_WRITE: "Memory write",
    EventType.DOCUMENT_INDEXED: "Document indexed",
    EventType.RETRIEVAL_QUERIED: "Retrieval",
    EventType.RETRIEVAL_RERANKED: "Rerank",
    EventType.SKILL_LOADED: "Skill loaded",
    EventType.SKILL_ACTIVATED: "Skill used",
    EventType.MCP_CONNECTED: "MCP connected",
    EventType.MCP_DISCONNECTED: "MCP disconnected",
    EventType.MCP_TOOLS_DISCOVERED: "MCP tools discovered",
    EventType.TOOL_CALLED: "Tool call",
    EventType.TOOL_RETURNED: "Tool response",
    EventType.TOOL_DENIED: "Tool denied",
    EventType.AGENT_LOOP_STARTED: "Agent loop started",
    EventType.AGENT_LOOP_ITERATION: "Iteration",
    EventType.AGENT_LOOP_FINISHED: "Agent loop finished",
    EventType.HANDOFF: "Handoff",
    EventType.SUBAGENT_STARTED: "Subagent started",
    EventType.SUBAGENT_FINISHED: "Subagent finished",
    EventType.PLAN_CREATED: "Plan created",
    EventType.GRAPH_STARTED: "Graph started",
    EventType.GRAPH_NODE_STARTED: "Node started",
    EventType.GRAPH_NODE_FINISHED: "Node finished",
    EventType.GRAPH_NODE_FAILED: "Node failed",
    EventType.GRAPH_FINISHED: "Graph finished",
    EventType.STATE_UPDATED: "State updated",
    EventType.CHECKPOINT_CREATED: "Checkpoint",
    EventType.CHECKPOINT_RESTORED: "Checkpoint restored",
    EventType.SANDBOX_EXECUTED: "Sandbox",
    EventType.HUMAN_APPROVAL_REQUESTED: "Approval requested",
    EventType.HUMAN_APPROVED: "Approved",
    EventType.HUMAN_REJECTED: "Rejected",
    EventType.HUMAN_FEEDBACK: "Feedback",
    EventType.GUARDRAIL_TRIGGERED: "Guardrail",
    EventType.GUARDRAIL_PASSED: "Guardrail passed",
    EventType.EVALUATION_SCORED: "Evaluation",
    EventType.REPLAY_SUBSTITUTED: "Replay substitution",
}

_ERROR_EVENTS = frozenset(
    {
        EventType.RUN_FAILED,
        EventType.GRAPH_NODE_FAILED,
        EventType.TOOL_DENIED,
        EventType.HUMAN_REJECTED,
    }
)
_WARNING_EVENTS = frozenset({EventType.GUARDRAIL_TRIGGERED, EventType.REPLAY_SUBSTITUTED})

# Payload keys never worth sending in a timeline row: the tab that owns them
# renders them properly, and they are the large ones (UI §50).
_BULKY_KEYS = frozenset({"messages", "tool_specs", "message", "provenance", "decision", "hits"})
_PREVIEW_CHARS = 280

EntryStatus = Literal["ok", "error", "warning", "running"]
ToolStatus = Literal["success", "error", "denied"]


def _text(value: Any, limit: int = _PREVIEW_CHARS) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _digest(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _message_text(message: dict[str, Any]) -> str:
    parts = message.get("content") or []
    if isinstance(parts, str):
        return parts
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            chunks.append(str(part.get("text", "")))
        elif part.get("type") == "tool_call":
            chunks.append(f"→ {part.get('name', 'tool')}({part.get('arguments', {})})")
        elif part.get("type") == "tool_result":
            chunks.append(f"← {part.get('content', '')}")
    return "\n".join(c for c in chunks if c)


# Identity ---------------------------------------------------------------------
def agent_of(manifest: RunManifest) -> str | None:
    """The agent (or graph) a run belongs to, from its dependencies.

    A run is usually named after its root, so a dependency with that name
    wins; otherwise a graph outranks the agents it orchestrates, which is what
    the runs table wants in its AGENT column (UI §7).
    """
    roots = [d for d in manifest.dependencies if d.kind in ("agent", "graph")]
    for dep in roots:
        if dep.name == manifest.name:
            return dep.name
    for kind in ("graph", "agent"):
        for dep in roots:
            if dep.kind == kind:
                return dep.name
    return None


def model_of(manifest: RunManifest) -> str | None:
    for dep in manifest.dependencies:
        if dep.kind == "model":
            return dep.name
    return None


def environment_of(manifest: RunManifest) -> str:
    """UI §42: every run carries its environment."""
    value = manifest.metadata.get("environment")
    return value if isinstance(value, str) and value else "development"


def user_of(manifest: RunManifest) -> str | None:
    value = manifest.metadata.get("user")
    return value if isinstance(value, str) and value else None


def session_of(manifest: RunManifest) -> str | None:
    value = manifest.metadata.get("session_id") or manifest.metadata.get("session")
    return value if isinstance(value, str) and value else None


def eval_score_of(events: Sequence[Event]) -> float | None:
    scores = [
        _float(e.payload.get("score"))
        for e in events
        if e.type is EventType.EVALUATION_SCORED and e.payload.get("score") is not None
    ]
    return sum(scores) / len(scores) if scores else None


def run_summary(manifest: RunManifest, *, eval_score: float | None = None) -> s.RunSummary:
    """One row of the runs table (UI §7)."""
    return s.RunSummary(
        id=manifest.id,
        name=manifest.name,
        status=manifest.status.value,
        project=manifest.project,
        environment=environment_of(manifest),
        agent=agent_of(manifest),
        user=user_of(manifest),
        model=model_of(manifest),
        session_id=session_of(manifest),
        parent_run_id=manifest.parent_run_id,
        started_at=manifest.started_at,
        ended_at=manifest.ended_at,
        duration_ms=manifest.duration_ms or 0.0,
        cost=manifest.cost.total,
        input_tokens=manifest.usage.input_tokens,
        output_tokens=manifest.usage.output_tokens,
        model_calls=manifest.usage.model_calls,
        tool_calls=manifest.usage.tool_calls,
        event_count=manifest.event_count,
        error=manifest.error,
        tags=list(manifest.tags),
        eval_score=eval_score,
    )


def cost_breakdown(manifest: RunManifest) -> s.CostBreakdown:
    cost = manifest.cost
    return s.CostBreakdown(
        model=cost.model,
        tool=cost.tool,
        embedding=cost.embedding,
        retrieval=cost.retrieval,
        sandbox=cost.sandbox,
        total=cost.total,
        currency=cost.currency,
    )


def panel_counts(recorded: RecordedRun) -> s.PanelCounts:
    """How much each tab holds, so the UI can mark the empty ones (UI §8)."""
    counts: dict[EventType, int] = {}
    for event in recorded.events:
        counts[event.type] = counts.get(event.type, 0) + 1
    graph_nodes = counts.get(EventType.GRAPH_NODE_STARTED, 0)
    subagents = counts.get(EventType.SUBAGENT_STARTED, 0)
    return s.PanelCounts(
        timeline=len(recorded.events),
        graph=graph_nodes + subagents,
        context=counts.get(EventType.CONTEXT_ASSEMBLED, 0),
        model=len(recorded.model_calls),
        prompt=len(recorded.model_calls),
        memory=counts.get(EventType.MEMORY_READ, 0) + counts.get(EventType.MEMORY_WRITE, 0),
        tools=len(recorded.tool_calls),
        mcp=counts.get(EventType.MCP_CONNECTED, 0),
        skills=counts.get(EventType.SKILL_LOADED, 0),
        guardrails=(
            counts.get(EventType.GUARDRAIL_TRIGGERED, 0) + counts.get(EventType.GUARDRAIL_PASSED, 0)
        ),
        approvals=counts.get(EventType.HUMAN_APPROVAL_REQUESTED, 0),
    )


def run_detail(recorded: RecordedRun) -> s.RunDetail:
    """The run header (UI §8): everything above the tabs, and nothing below."""
    manifest = recorded.manifest
    failure = next(
        (e for e in recorded.events if e.type is EventType.RUN_FAILED),
        None,
    )
    return s.RunDetail(
        run=run_summary(manifest, eval_score=eval_score_of(recorded.events)),
        input=manifest.input,
        output=manifest.output,
        error=manifest.error,
        traceback=_str_or_none(failure.payload.get("traceback")) if failure else None,
        dependencies=[
            s.DependencyView(
                kind=d.kind,
                name=d.name,
                version=d.version,
                fingerprint=d.fingerprint,
                metadata=dict(d.metadata),
            )
            for d in manifest.dependencies
        ],
        panels=panel_counts(recorded),
        cost=cost_breakdown(manifest),
        metadata=dict(manifest.metadata),
    )


# Timeline ---------------------------------------------------------------------
def _entry_detail(event: Event) -> str:
    payload = event.payload
    if event.type in (EventType.MODEL_CALLED, EventType.MODEL_RESPONSE):
        return _text(payload.get("model"))
    if event.type in (EventType.TOOL_CALLED, EventType.TOOL_RETURNED, EventType.TOOL_DENIED):
        return f"{payload.get('name', '')}()"
    if event.type is EventType.CONTEXT_ASSEMBLED:
        decision = payload.get("decision") or {}
        return f"{_int(decision.get('used'))} / {_int(decision.get('budget'))} tokens"
    if event.type in (EventType.SKILL_LOADED, EventType.SKILL_ACTIVATED):
        return f"{payload.get('skill', '')} v{payload.get('version', '')}"
    if event.type in (EventType.GRAPH_NODE_STARTED, EventType.GRAPH_NODE_FINISHED):
        return _text(payload.get("node"))
    if event.type in (EventType.SUBAGENT_STARTED, EventType.SUBAGENT_FINISHED):
        return _text(payload.get("subagent"))
    if event.type in (EventType.MEMORY_READ, EventType.MEMORY_WRITE):
        return _text(payload.get("content") or payload.get("query"))
    if event.type in (EventType.GUARDRAIL_TRIGGERED, EventType.GUARDRAIL_PASSED):
        return _text(payload.get("guardrail") or payload.get("policy"))
    if event.type in (EventType.MCP_CONNECTED, EventType.MCP_TOOLS_DISCOVERED):
        return _text(payload.get("server"))
    if event.type is EventType.HUMAN_APPROVAL_REQUESTED:
        return _text(payload.get("action"))
    if event.type is EventType.RUN_FAILED:
        return _text(payload.get("error"))
    return ""


def _entry_status(event: Event) -> EntryStatus:
    if event.type in _ERROR_EVENTS:
        return "error"
    if event.type in _WARNING_EVENTS:
        return "warning"
    if event.payload.get("error"):
        return "error"
    if event.payload.get("is_error"):
        return "error"
    return "ok"


def _trimmed(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (_text(value) if isinstance(value, str) else value)
        for key, value in payload.items()
        if key not in _BULKY_KEYS
    }


def timeline(recorded: RecordedRun, *, after_seq: int = 0, limit: int = 200) -> s.TimelineView:
    """The run timeline (UI §9), paginated by ``seq`` so long runs stay fast."""
    started = recorded.manifest.started_at
    depth_of: dict[str, int] = {}
    entries: list[s.TimelineEntry] = []
    remaining = [e for e in recorded.events if e.seq > after_seq]
    for event in remaining[:limit]:
        parent = event.parent_span_id
        depth = depth_of.get(parent, 0) + 1 if parent else 0
        if event.span_id:
            depth_of[event.span_id] = depth
        entries.append(
            s.TimelineEntry(
                seq=event.seq,
                type=event.type.value,
                timestamp=event.timestamp,
                offset_ms=(event.timestamp - started).total_seconds() * 1000.0,
                span_id=event.span_id,
                parent_span_id=event.parent_span_id,
                depth=depth,
                label=EVENT_LABELS.get(event.type, event.type.value.replace("_", " ").title()),
                detail=_entry_detail(event),
                status=_entry_status(event),
                duration_ms=(
                    _float(event.payload.get("latency_ms"))
                    if event.payload.get("latency_ms") is not None
                    else None
                ),
                payload=_trimmed(event.payload),
            )
        )
    consumed = after_seq if not entries else entries[-1].seq
    return s.TimelineView(
        run_id=recorded.id,
        started_at=started,
        entries=entries,
        total=len(recorded.events),
        next_seq=consumed if len(remaining) > limit else None,
    )


def events_page(recorded: RecordedRun, *, after_seq: int = 0, limit: int = 200) -> s.EventPage:
    """Raw event records, for the payload viewer behind each timeline row."""
    remaining = [e for e in recorded.events if e.seq > after_seq]
    page = remaining[:limit]
    return s.EventPage(
        run_id=recorded.id,
        events=[e.to_record() for e in page],
        total=len(recorded.events),
        next_seq=page[-1].seq if page and len(remaining) > limit else None,
    )


# Execution graph --------------------------------------------------------------
def _graph_nodes(recorded: RecordedRun) -> tuple[list[s.GraphNodeView], list[s.GraphEdgeView]]:
    nodes: dict[str, s.GraphNodeView] = {}
    order: list[str] = []
    for event in recorded.events:
        payload = event.payload
        name = _str_or_none(payload.get("node"))
        if name is None:
            continue
        if event.type is EventType.GRAPH_NODE_STARTED:
            nodes[name] = s.GraphNodeView(
                id=name,
                label=name,
                kind=str(payload.get("kind", "node")),
                status="running",
                started_at=event.timestamp,
                seq=event.seq,
                detail={"step": payload.get("step")},
            )
            order.append(name)
        elif event.type in (EventType.GRAPH_NODE_FINISHED, EventType.GRAPH_NODE_FAILED):
            node = nodes.get(name)
            if node is None:
                continue
            failed = event.type is EventType.GRAPH_NODE_FAILED
            nodes[name] = node.model_copy(
                update={
                    "status": "error" if failed else "ok",
                    "duration_ms": _float(payload.get("duration_ms"))
                    or _float(payload.get("latency_ms")),
                    "detail": node.detail | {"error": payload.get("error")}
                    if failed
                    else node.detail,
                }
            )
    edges = [
        s.GraphEdgeView(source=order[i], target=order[i + 1], kind="flow")
        for i in range(len(order) - 1)
    ]
    return [nodes[name] for name in order], edges


def _agent_nodes(recorded: RecordedRun) -> tuple[list[s.GraphNodeView], list[s.GraphEdgeView]]:
    root = agent_of(recorded.manifest) or recorded.manifest.name
    nodes: list[s.GraphNodeView] = [s.GraphNodeView(id=root, label=root, kind="agent", status="ok")]
    edges: list[s.GraphEdgeView] = []
    seen = {root}
    for event in recorded.events:
        payload = event.payload
        if event.type is EventType.SUBAGENT_STARTED:
            name = str(payload.get("subagent", ""))
            parent = str(payload.get("parent") or root)
            if name and name not in seen:
                seen.add(name)
                nodes.append(
                    s.GraphNodeView(
                        id=name,
                        label=name,
                        kind="subagent",
                        status="running",
                        started_at=event.timestamp,
                        seq=event.seq,
                        parent=parent,
                        detail={"task": _text(payload.get("task"))},
                    )
                )
                edges.append(s.GraphEdgeView(source=parent, target=name, kind="spawn"))
        elif event.type is EventType.SUBAGENT_FINISHED:
            name = str(payload.get("subagent", ""))
            for index, node in enumerate(nodes):
                if node.id == name:
                    failed = payload.get("stop_reason") == "error"
                    nodes[index] = node.model_copy(update={"status": "error" if failed else "ok"})
        elif event.type is EventType.HANDOFF:
            source = str(payload.get("sender") or root)
            target = str(payload.get("receiver") or "")
            if not target:
                continue
            if target not in seen:
                seen.add(target)
                nodes.append(s.GraphNodeView(id=target, label=target, kind="agent", status="ok"))
            edges.append(
                s.GraphEdgeView(
                    source=source,
                    target=target,
                    kind="handoff",
                    label=_text(payload.get("reason"), 60),
                )
            )
    return nodes, edges


def _linear_nodes(recorded: RecordedRun) -> tuple[list[s.GraphNodeView], list[s.GraphEdgeView]]:
    nodes: list[s.GraphNodeView] = [
        s.GraphNodeView(id="start", label="START", kind="start", status="ok")
    ]
    for call in recorded.model_calls:
        nodes.append(
            s.GraphNodeView(
                id=f"model-{call.index}",
                label=call.model or "model",
                kind="model",
                status="error" if call.error else "ok",
                seq=call.seq,
                duration_ms=call.latency_ms,
            )
        )
    for tool_call in recorded.tool_calls:
        nodes.append(
            s.GraphNodeView(
                id=f"tool-{tool_call.index}",
                label=f"{tool_call.name}()",
                kind="tool",
                status="error" if tool_call.is_error or tool_call.denied else "ok",
                seq=tool_call.seq,
                duration_ms=tool_call.latency_ms,
            )
        )
    ordered = [nodes[0], *sorted(nodes[1:], key=lambda n: n.seq or 0)]
    status = "error" if recorded.manifest.error else "ok"
    ordered.append(s.GraphNodeView(id="output", label="OUTPUT", kind="output", status=status))
    edges = [
        s.GraphEdgeView(source=ordered[i].id, target=ordered[i + 1].id, kind="flow")
        for i in range(len(ordered) - 1)
    ]
    return ordered, edges


def graph(recorded: RecordedRun) -> s.GraphView:
    """The execution graph (UI §10): a DAG, an agent tree, or a chain."""
    types = {e.type for e in recorded.events}
    if EventType.GRAPH_STARTED in types:
        nodes, edges = _graph_nodes(recorded)
        entry = next(
            (
                _str_or_none(e.payload.get("entry"))
                for e in recorded.events
                if e.type is EventType.GRAPH_STARTED
            ),
            None,
        )
        return s.GraphView(run_id=recorded.id, shape="graph", root=entry, nodes=nodes, edges=edges)
    if types & {EventType.SUBAGENT_STARTED, EventType.HANDOFF}:
        nodes, edges = _agent_nodes(recorded)
        return s.GraphView(
            run_id=recorded.id,
            shape="agents",
            root=nodes[0].id if nodes else None,
            nodes=nodes,
            edges=edges,
        )
    nodes, edges = _linear_nodes(recorded)
    return s.GraphView(run_id=recorded.id, shape="linear", root="start", nodes=nodes, edges=edges)


# Context ----------------------------------------------------------------------
def _visible(sensitivity: str, ceiling: str) -> bool:
    return SENSITIVITY_ORDER.get(sensitivity, 1) <= SENSITIVITY_ORDER.get(ceiling, 1)


def context(recorded: RecordedRun, *, role: str = "owner") -> s.ContextView:
    """The context inspector (UI §11) and its composition (UI §12).

    Items above the role's sensitivity ceiling are returned as placeholders:
    the reader learns that something was withheld without reading it (UI §54).
    """
    ceiling = ROLE_SENSITIVITY.get(role, "internal")
    assemblies: list[s.ContextAssembly] = []
    for event in recorded.events_of(EventType.CONTEXT_ASSEMBLED):
        payload = event.payload
        decision = payload.get("decision") or {}
        provenance = {
            str(p.get("id")): p for p in payload.get("provenance") or [] if isinstance(p, dict)
        }
        items: list[s.ContextItemView] = []
        for included in decision.get("included") or []:
            items.append(_context_item(included, provenance.get(str(included.get("id"))), ceiling))
        for excluded in decision.get("excluded") or []:
            item = _context_item(excluded, provenance.get(str(excluded.get("id"))), ceiling)
            items.append(
                item.model_copy(
                    update={"included": False, "excluded_reason": excluded.get("reason")}
                )
            )
        assemblies.append(
            s.ContextAssembly(
                seq=event.seq,
                context=str(payload.get("context", "context")),
                version=str(payload.get("version", "1")),
                query=_str_or_none(payload.get("query")),
                fingerprint=_str_or_none(payload.get("fingerprint")),
                budget=_int(decision.get("budget")),
                used=_int(decision.get("used")),
                by_kind={str(k): _int(v) for k, v in (decision.get("by_kind") or {}).items()},
                items=items,
                dropped_untrusted=[str(i) for i in payload.get("dropped_untrusted") or []],
                stale=[str(i) for i in payload.get("stale") or []],
                assembled_in_ms=_float(payload.get("assembled_in_ms")),
            )
        )
    retrievals = [
        e.payload
        for e in recorded.events_of(EventType.CONTEXT_RETRIEVED, EventType.RETRIEVAL_QUERIED)
    ]
    return s.ContextView(run_id=recorded.id, assemblies=assemblies, retrievals=retrievals)


def _context_item(
    entry: dict[str, Any], provenance: dict[str, Any] | None, ceiling: str
) -> s.ContextItemView:
    prov = provenance or {}
    sensitivity = str(prov.get("sensitivity", "internal"))
    visible = _visible(sensitivity, ceiling)
    retrieved = prov.get("retrieved_at")
    return s.ContextItemView(
        id=str(entry.get("id", "")),
        kind=str(entry.get("kind") or prov.get("kind") or "knowledge"),
        title=(entry.get("title") or prov.get("title")) if visible else None,
        source=_str_or_none(prov.get("source")) if visible else None,
        record=_str_or_none(prov.get("record")) if visible else None,
        version=_str_or_none(prov.get("version")),
        uri=_str_or_none(prov.get("uri")) if visible else None,
        retrieved_at=_timestamp(retrieved),
        hash=_str_or_none(prov.get("hash")),
        tokens=_int(entry.get("tokens") or prov.get("tokens")),
        relevance=_float(entry.get("score") or prov.get("relevance")),
        authority=_float(prov.get("authority"), 0.5),
        trust_level=str(prov.get("trust_level", "internal")),
        sensitivity=sensitivity,
        verified=prov.get("verified"),
        redacted=not visible,
        redaction_reason=None if visible else f"sensitivity {sensitivity!r} exceeds your access",
    )


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


# Model and prompt -------------------------------------------------------------
def model(recorded: RecordedRun) -> s.ModelView:
    """Every model call with its parameters, tokens, cost and latency (UI §14)."""
    calls: list[s.ModelCallView] = []
    providers: list[str] = []
    models: list[str] = []
    for call in recorded.model_calls:
        request = call.request
        response = call.response
        usage = response.usage if response else None
        cost = response.cost if response else None
        if call.provider and call.provider not in providers:
            providers.append(call.provider)
        if call.model and call.model not in models:
            models.append(call.model)
        calls.append(
            s.ModelCallView(
                index=call.index,
                seq=call.seq,
                provider=call.provider,
                model=call.model,
                temperature=request.get("temperature"),
                max_tokens=request.get("max_tokens"),
                top_p=request.get("top_p"),
                seed=request.get("seed"),
                stop=[str(x) for x in request.get("stop") or []],
                reasoning={
                    k: v
                    for k, v in (request.get("provider_options") or {}).items()
                    if "reason" in k or "think" in k
                },
                output_schema=request.get("output_schema"),
                tools=list(call.tools),
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                cached_tokens=usage.cache_read_tokens if usage else 0,
                reasoning_tokens=usage.reasoning_tokens if usage else 0,
                cost=cost.total if cost else 0.0,
                cost_source=cost.source if cost else "unknown",
                latency_ms=call.latency_ms,
                finish_reason=response.finish_reason if response else None,
                request_fingerprint=call.request_fingerprint,
                error=call.error,
                text=_text(call.text, 2000),
            )
        )
    return s.ModelView(
        run_id=recorded.id,
        calls=calls,
        providers=providers,
        models=models,
        total_cost=sum(c.cost for c in calls),
        total_latency_ms=sum(c.latency_ms for c in calls),
    )


def _prompt_message(message: dict[str, Any]) -> s.PromptMessageView:
    text = _message_text(message)
    return s.PromptMessageView(
        role=str(message.get("role", "user")),
        name=_str_or_none(message.get("name")),
        text=text,
        hash=_digest(text),
        tokens=max(1, len(text) // 4) if text else 0,
    )


def prompt(recorded: RecordedRun) -> s.PromptView:
    """The exact prompt each call used, split by role (UI §15)."""
    calls: list[s.PromptCallView] = []
    for call in recorded.model_calls:
        view = s.PromptCallView(
            index=call.index,
            seq=call.seq,
            model=call.model,
            fingerprint=call.request_fingerprint,
        )
        for raw in call.request.get("messages") or []:
            if not isinstance(raw, dict):
                continue
            message = _prompt_message(raw)
            bucket = {
                "system": view.system,
                "developer": view.developer,
                "user": view.user,
                "assistant": view.assistant,
                "tool": view.tool,
            }.get(message.role)
            if bucket is None:
                view.user.append(message)
            else:
                bucket.append(message)
        view.tool_instructions = [
            s.PromptMessageView(
                role="tool_definition",
                name=str(spec.get("name", "")),
                text=str(spec.get("description", "")),
                hash=_digest(str(spec.get("description", ""))),
            )
            for spec in call.request.get("tool_specs") or []
            if isinstance(spec, dict)
        ]
        calls.append(view)
    skills = [
        s.PromptMessageView(
            role="skill",
            name=str(e.payload.get("skill", "")),
            text=str(e.payload.get("description", "")),
            hash=str(e.payload.get("fingerprint") or ""),
        )
        for e in recorded.events_of(EventType.SKILL_LOADED)
    ]
    for view in calls:
        view.skill_instructions = skills
    return s.PromptView(run_id=recorded.id, calls=calls)


# Memory -----------------------------------------------------------------------
def memory(recorded: RecordedRun) -> s.MemoryView:
    """Memory reads and writes with their confidence and age (UI §16)."""
    operations: list[s.MemoryOpView] = []
    for event in recorded.events_of(EventType.MEMORY_READ, EventType.MEMORY_WRITE):
        payload = event.payload
        if event.type is EventType.MEMORY_WRITE:
            operations.append(
                s.MemoryOpView(
                    seq=event.seq,
                    operation="write",
                    memory=str(payload.get("memory", "")),
                    kind=str(payload.get("kind", "")),
                    id=_str_or_none(payload.get("id")),
                    content=_text(payload.get("content")),
                    importance=payload.get("importance"),
                    provider=_str_or_none(payload.get("provider")),
                    timestamp=event.timestamp,
                )
            )
            continue
        for hit in payload.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            operations.append(
                s.MemoryOpView(
                    seq=event.seq,
                    operation="read",
                    memory=str(payload.get("memory", "")),
                    kind=str(hit.get("kind", "")),
                    id=_str_or_none(hit.get("id")),
                    content=_text(hit.get("content")),
                    query=_str_or_none(payload.get("query")),
                    score=hit.get("score"),
                    confidence=hit.get("confidence"),
                    created_at=_timestamp(hit.get("created_at")),
                    timestamp=event.timestamp,
                )
            )
    return s.MemoryView(run_id=recorded.id, operations=operations)


# Tools and MCP ----------------------------------------------------------------
def _mcp_tool_names(recorded: RecordedRun) -> dict[str, str]:
    """Tool name -> MCP server, so the tools panel can attribute each call."""
    owners: dict[str, str] = {}
    for event in recorded.events_of(EventType.MCP_TOOLS_DISCOVERED):
        server = str(event.payload.get("server", ""))
        for tool in event.payload.get("tools") or []:
            if isinstance(tool, dict) and tool.get("name"):
                owners[str(tool["name"])] = server
    return owners


def tools(recorded: RecordedRun) -> s.ToolView:
    """Every tool call, its arguments, result, duration and status (UI §17)."""
    owners = _mcp_tool_names(recorded)
    risks = {
        str(e.payload.get("tool_call_id")): str(e.payload.get("risk_level") or "")
        for e in recorded.events_of(EventType.TOOL_CALLED)
    }
    calls: list[s.ToolCallView] = []
    for call in recorded.tool_calls:
        status: ToolStatus = "denied" if call.denied else ("error" if call.is_error else "success")
        calls.append(
            s.ToolCallView(
                index=call.index,
                seq=call.seq,
                tool_call_id=call.tool_call_id,
                name=call.name,
                version=call.version,
                fingerprint=call.fingerprint,
                server=owners.get(call.name),
                arguments=dict(call.arguments),
                result=call.result,
                status=status,
                duration_ms=call.latency_ms,
                replayable=risks.get(call.tool_call_id) == "low",
            )
        )
    return s.ToolView(run_id=recorded.id, calls=calls)


def mcp(recorded: RecordedRun, *, previous: RecordedRun | None = None) -> s.McpView:
    """MCP servers, their tools, and whether they changed since the last run (UI §18)."""
    versions = {
        d.name: d for d in recorded.manifest.dependencies if d.kind in ("mcp_server", "mcp")
    }
    before = (
        {d.name: d for d in previous.manifest.dependencies if d.kind in ("mcp_server", "mcp")}
        if previous
        else {}
    )
    call_counts: dict[str, int] = {}
    for call in recorded.tool_calls:
        call_counts[call.name] = call_counts.get(call.name, 0) + 1

    servers: dict[str, s.McpServerView] = {}
    for event in recorded.events_of(EventType.MCP_CONNECTED):
        payload = event.payload
        name = str(payload.get("server", ""))
        info = payload.get("server_info") or {}
        dependency = versions.get(name)
        servers[name] = s.McpServerView(
            server=name,
            version=(
                dependency.version
                if dependency
                else str(info.get("version", "unversioned"))
                if isinstance(info, dict)
                else "unversioned"
            ),
            transport=_str_or_none(payload.get("transport")),
            fingerprint=_str_or_none(payload.get("fingerprint")),
            connected_at=event.timestamp,
            latency_ms=_float(payload.get("latency_ms")),
        )
    for event in recorded.events_of(EventType.MCP_TOOLS_DISCOVERED):
        payload = event.payload
        name = str(payload.get("server", ""))
        server = servers.get(name) or s.McpServerView(server=name)
        server.tools = [
            s.McpToolView(
                name=str(tool.get("name", "")),
                fingerprint=_str_or_none(tool.get("fingerprint")),
                risk_level=_str_or_none(tool.get("risk_level")),
                calls=call_counts.get(str(tool.get("name", "")), 0),
            )
            for tool in payload.get("tools") or []
            if isinstance(tool, dict)
        ]
        server.resources = [str(r) for r in payload.get("resources") or []]
        servers[name] = server
    for name, server in servers.items():
        old = before.get(name)
        if old is not None and (
            old.version != server.version
            or (old.fingerprint and old.fingerprint != server.fingerprint)
        ):
            server.changed_since_previous_run = True
            server.previous_version = old.version
    return s.McpView(run_id=recorded.id, servers=list(servers.values()))


def skills(recorded: RecordedRun) -> s.SkillsView:
    """Skills, and how far each got: discovered, loaded, used (UI §19)."""
    views: dict[str, s.SkillUseView] = {}
    for event in recorded.events_of(EventType.SKILL_LOADED):
        payload = event.payload
        name = str(payload.get("skill", ""))
        mode = str(payload.get("mode", ""))
        views[name] = s.SkillUseView(
            skill=name,
            version=str(payload.get("version", "unversioned")),
            fingerprint=_str_or_none(payload.get("fingerprint")),
            state="discovered" if mode == "progressive" else "loaded",
            description=str(payload.get("description", "")),
            resources=[str(r) for r in payload.get("resources") or []],
            scripts=[str(r) for r in payload.get("scripts") or []],
            allowed_tools=[str(r) for r in payload.get("allowed_tools") or []],
        )
    for event in recorded.events_of(EventType.SKILL_ACTIVATED):
        name = str(event.payload.get("skill", ""))
        view = views.get(name) or s.SkillUseView(
            skill=name, version=str(event.payload.get("version", "unversioned"))
        )
        view.state = "used"
        view.activations += 1
        views[name] = view
    return s.SkillsView(run_id=recorded.id, skills=list(views.values()))


def guardrails(recorded: RecordedRun) -> list[s.GuardrailView]:
    views: list[s.GuardrailView] = []
    for event in recorded.events_of(EventType.GUARDRAIL_TRIGGERED, EventType.GUARDRAIL_PASSED):
        payload = event.payload
        views.append(
            s.GuardrailView(
                seq=event.seq,
                guardrail=str(payload.get("guardrail") or payload.get("policy") or ""),
                stage=str(payload.get("stage", "")),
                triggered=event.type is EventType.GUARDRAIL_TRIGGERED,
                action=_str_or_none(payload.get("action")),
                detail=dict(payload),
            )
        )
    return views


def approvals(recorded: RecordedRun) -> list[s.ApprovalView]:
    """Human decisions, which UI §35 requires to be part of the run."""
    views: dict[str, s.ApprovalView] = {}
    for event in recorded.events_of(EventType.HUMAN_APPROVAL_REQUESTED):
        payload = event.payload
        request_id = str(payload.get("request_id", ""))
        views[request_id] = s.ApprovalView(
            request_id=request_id,
            action=str(payload.get("action", "")),
            risk=_str_or_none(payload.get("risk")),
            details=dict(payload.get("details") or {}),
            requested_at=event.timestamp,
            run_id=recorded.id,
        )
    for event in recorded.events_of(EventType.HUMAN_APPROVED, EventType.HUMAN_REJECTED):
        payload = event.payload
        request_id = str(payload.get("request_id", ""))
        view = views.get(request_id)
        if view is None:
            continue
        views[request_id] = view.model_copy(
            update={
                "decision": "approved" if event.type is EventType.HUMAN_APPROVED else "rejected",
                "decided_at": event.timestamp,
                "by": _str_or_none(payload.get("by")),
                "reason": _str_or_none(payload.get("reason")),
            }
        )
    return list(views.values())


def scores(recorded: RecordedRun) -> list[s.ScoreView]:
    """Every verdict this run recorded, and which run each one judged.

    An evaluation records its scores on the *evaluating* run, not on the run
    under test, so this is the join that lets a subject run show its score and
    the runs table filter on it (UI §7).
    """
    found: list[s.ScoreView] = []
    for event in recorded.events_of(EventType.EVALUATION_SCORED):
        payload = event.payload
        subject = _str_or_none(payload.get("subject_run_id")) or recorded.id
        found.append(
            s.ScoreView(
                subject_run_id=subject,
                evaluator=str(payload.get("evaluator", "")),
                version=str(payload.get("version", "1")),
                value=_float(payload.get("value")),
                passed=bool(payload.get("passed")),
                label=_str_or_none(payload.get("label")),
                reason=str(payload.get("reason", "")),
                threshold=payload.get("threshold"),
                scored_at=event.timestamp,
                scored_in_run_id=recorded.id,
            )
        )
    return found


def mean_score(views: Sequence[s.ScoreView]) -> float | None:
    """The weighted-free mean a run is filtered and sorted by."""
    if not views:
        return None
    return sum(view.value for view in views) / len(views)


def dependency_versions(manifest: RunManifest) -> dict[str, str]:
    """``kind:name -> version``, the unit the overview's change list diffs."""
    return {d.key: d.version for d in manifest.dependencies}


def all_projections(recorded: RecordedRun, *, role: str = "owner") -> dict[str, Any]:
    """Every run-scoped view at once. The cloud stores this at ingest (UI §50)."""
    return {
        "detail": run_detail(recorded).model_dump(mode="json"),
        "timeline": timeline(recorded).model_dump(mode="json"),
        "graph": graph(recorded).model_dump(mode="json"),
        "context": context(recorded, role=role).model_dump(mode="json"),
        "model": model(recorded).model_dump(mode="json"),
        "prompt": prompt(recorded).model_dump(mode="json"),
        "memory": memory(recorded).model_dump(mode="json"),
        "tools": tools(recorded).model_dump(mode="json"),
        "mcp": mcp(recorded).model_dump(mode="json"),
        "skills": skills(recorded).model_dump(mode="json"),
        "guardrails": [g.model_dump(mode="json") for g in guardrails(recorded)],
        "approvals": [a.model_dump(mode="json") for a in approvals(recorded)],
        "scores": [v.model_dump(mode="json") for v in scores(recorded)],
    }


def iter_summaries(manifests: Iterable[RunManifest]) -> list[s.RunSummary]:
    return [run_summary(m) for m in manifests]
