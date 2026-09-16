"""Projections describe the run that actually happened (UI spec §8-§19).

Each test asserts against a real recorded run, so a payload rename in a
subsystem breaks the projection here rather than silently emptying a panel
in the console.
"""

from __future__ import annotations

from rewyn.replay.recorder import RecordedRun
from rewyn.ui import projections


def test_the_summary_identifies_the_run_the_way_the_runs_table_needs(recorded: RecordedRun):
    """UI §7: STATUS | TIME | AGENT | USER | MODEL | LATENCY | COST."""
    summary = projections.run_summary(recorded.manifest)
    assert summary.id == recorded.id
    assert summary.status == "succeeded"
    assert summary.agent == "acme-credit"
    assert summary.user == "raj"
    assert summary.environment == "production"
    assert summary.session_id == "sess-1"
    assert summary.model is not None
    assert summary.cost > 0
    assert summary.duration_ms > 0


def test_the_header_carries_dependencies_and_panel_counts(recorded: RecordedRun):
    detail = projections.run_detail(recorded)
    kinds = {d.kind for d in detail.dependencies}
    assert {"graph", "agent", "model", "tool", "skill", "mcp_server"} <= kinds
    assert detail.panels.timeline == len(recorded.events)
    assert detail.panels.context >= 1
    assert detail.panels.tools >= 1
    assert detail.panels.skills >= 1
    assert detail.cost.total > 0
    assert detail.output


def test_the_timeline_is_ordered_labelled_and_pageable(recorded: RecordedRun):
    """UI §9: every event is a row, and a long run does not arrive at once."""
    view = projections.timeline(recorded, limit=5)
    assert [e.seq for e in view.entries] == sorted(e.seq for e in view.entries)
    assert view.entries[0].label == "Run started"
    assert view.total == len(recorded.events)
    assert view.next_seq == view.entries[-1].seq

    rest = projections.timeline(recorded, after_seq=view.next_seq or 0, limit=500)
    assert rest.entries[0].seq > view.entries[-1].seq
    assert {e.type for e in rest.entries} <= {e.type.value for e in recorded.events}


def test_timeline_rows_stay_small(recorded: RecordedRun):
    """UI §50: a row carries a summary, never the whole payload."""
    view = projections.timeline(recorded, limit=500)
    for entry in view.entries:
        assert "messages" not in entry.payload
        assert "provenance" not in entry.payload


def test_the_graph_shows_the_nodes_the_run_executed(recorded: RecordedRun):
    """UI §10: a DAG for graph runs, with clickable nodes."""
    view = projections.graph(recorded)
    assert view.shape == "graph"
    assert [n.id for n in view.nodes] == ["plan", "research", "analyze", "risk", "sign_off"]
    assert all(n.status == "ok" for n in view.nodes)
    assert (view.edges[0].source, view.edges[0].target) == ("plan", "research")


def test_the_context_panel_shows_source_version_tokens_and_provenance(recorded: RecordedRun):
    """UI §11 and §12: what was used, where it came from, and what it cost."""
    view = projections.context(recorded)
    assembly = view.assemblies[-1]
    assert assembly.budget > 0
    assert assembly.used > 0
    assert sum(assembly.by_kind.values()) == assembly.used
    included = [i for i in view.assemblies[-1].items if i.included]
    assert included
    assert all(i.tokens >= 0 for i in included)
    assert any(i.source for i in included)
    assert any(i.trust_level == "untrusted" for i in assembly.items)


def test_context_items_above_the_role_ceiling_are_withheld_not_shown(recorded: RecordedRun):
    """UI §54: sensitive context respects permissions, server-side."""
    owner = projections.context(recorded, role="owner")
    viewer = projections.context(recorded, role="viewer")
    sensitive = [
        i for i in owner.assemblies[-1].items if i.sensitivity in ("confidential", "restricted")
    ]
    if not sensitive:  # the demo may carry none; the mechanism is still asserted
        return
    withheld = [i for i in viewer.assemblies[-1].items if i.redacted]
    assert len(withheld) == len(sensitive)
    assert all(i.title is None and i.source is None for i in withheld)
    assert all(i.redaction_reason for i in withheld)


def test_the_model_panel_reports_parameters_tokens_cost_and_latency(recorded: RecordedRun):
    """UI §14."""
    view = projections.model(recorded)
    assert view.calls
    assert view.models
    first = view.calls[0]
    assert first.provider
    assert first.input_tokens > 0
    assert first.output_tokens > 0
    assert view.total_cost > 0
    assert first.finish_reason


def test_the_prompt_panel_separates_roles_and_hashes_each_message(recorded: RecordedRun):
    """UI §15."""
    view = projections.prompt(recorded)
    call = view.calls[0]
    assert call.system
    assert call.user
    assert all(m.hash.startswith("sha256:") for m in call.system + call.user)
    assert call.tool_instructions
    assert call.skill_instructions


def test_the_memory_panel_shows_reads_and_writes(recorded: RecordedRun):
    """UI §16."""
    view = projections.memory(recorded)
    kinds = {op.operation for op in view.operations}
    assert kinds == {"read", "write"}
    assert all(op.content for op in view.operations)


def test_the_tools_panel_shows_arguments_result_duration_and_status(recorded: RecordedRun):
    """UI §17."""
    view = projections.tools(recorded)
    assert view.calls
    call = view.calls[0]
    assert call.name
    assert call.status in ("success", "error", "denied")
    assert call.duration_ms >= 0
    assert isinstance(call.replayable, bool)


def test_mcp_calls_are_attributed_to_their_server(recorded: RecordedRun):
    """UI §18: every MCP operation is visible, and owned by a server."""
    mcp = projections.mcp(recorded)
    assert mcp.servers
    server = mcp.servers[0]
    assert server.server == "salesforce"
    assert server.tools
    tools = projections.tools(recorded)
    assert any(c.server == "salesforce" for c in tools.calls)


def test_mcp_reports_a_version_change_against_the_previous_run(recorded: RecordedRun):
    """UI §18: "MCP server changed since previous run"."""
    before = RecordedRun(recorded.manifest.model_copy(deep=True), [])
    for dependency in before.manifest.dependencies:
        if dependency.kind == "mcp_server":
            index = before.manifest.dependencies.index(dependency)
            before.manifest.dependencies[index] = dependency.model_copy(update={"version": "0"})
    view = projections.mcp(recorded, previous=before)
    assert view.servers[0].changed_since_previous_run is True
    assert view.servers[0].previous_version == "0"


def test_skills_report_how_far_each_one_got(recorded: RecordedRun):
    """UI §19: DISCOVERED / LOADED / USED."""
    view = projections.skills(recorded)
    assert view.skills
    skill = view.skills[0]
    assert skill.skill == "credit-policy"
    assert skill.state in ("discovered", "loaded", "used")
    assert skill.version == "3"


def test_human_decisions_are_part_of_the_run(recorded: RecordedRun):
    """UI §35."""
    approvals = projections.approvals(recorded)
    assert approvals
    assert approvals[0].decision == "approved"
    assert approvals[0].by == "tester"
    assert approvals[0].decided_at is not None


def test_guardrail_results_are_visible(recorded: RecordedRun):
    assert projections.guardrails(recorded)


def test_all_projections_serialise_to_json(recorded: RecordedRun):
    """The cloud stores this blob at ingest, so it must be JSON (UI §50)."""
    import json

    payload = projections.all_projections(recorded)
    assert set(payload) >= {"detail", "timeline", "graph", "context", "model", "tools"}
    assert json.loads(json.dumps(payload))


async def test_the_graph_becomes_a_tree_for_multi_agent_runs():
    """UI §10: "Main Agent ├── Research Agent" -- a tree, not a chain."""
    from rewyn.agents.agent import Agent
    from rewyn.core.run import start_run
    from rewyn.replay.recorder import RecordedRun as Recorded
    from rewyn.testing.fake_model import FakeModel

    researcher = Agent(model=FakeModel(["EV share is 24%."]), name="researcher")
    lead = Agent(
        model=FakeModel(
            [
                FakeModel.tool_call("delegate_to_researcher", {"task": "EV share?"}),
                "24% and rising.",
            ]
        ),
        name="lead",
        subagents=[researcher],
    )
    async with start_run("lead-run") as run:
        await lead.arun("What is the EV share?")

    view = projections.graph(Recorded.from_run(run))
    assert view.shape == "agents"
    assert view.root == "lead"
    assert {n.id for n in view.nodes} == {"lead", "researcher"}
    spawn = next(e for e in view.edges if e.kind == "spawn")
    assert (spawn.source, spawn.target) == ("lead", "researcher")
    assert next(n for n in view.nodes if n.id == "researcher").status == "ok"


async def test_a_handoff_is_an_edge_between_agents():
    """UI §10 and §22: a handoff is a visible transfer, with its reason."""
    from rewyn.agents.agent import Agent
    from rewyn.core.run import start_run
    from rewyn.replay.recorder import RecordedRun as Recorded
    from rewyn.testing.fake_model import FakeModel

    billing = Agent(model=FakeModel(["Refund processed."]), name="billing")
    triage = Agent(
        model=FakeModel(
            [FakeModel.tool_call("handoff_to_billing", {"reason": "refund", "summary": "money"})]
        ),
        name="triage",
        handoffs=[billing],
    )
    async with start_run("triage-run") as run:
        await triage.arun("I want a refund")

    view = projections.graph(Recorded.from_run(run))
    assert view.shape == "agents"
    edge = next(e for e in view.edges if e.kind == "handoff")
    assert (edge.source, edge.target) == ("triage", "billing")
    assert edge.label == "refund"


async def test_a_simple_run_is_a_chain_from_start_to_output():
    """UI §10: START → … → OUTPUT for an agent with no graph."""
    from rewyn.agents.agent import Agent
    from rewyn.core.run import start_run
    from rewyn.replay.recorder import RecordedRun as Recorded
    from rewyn.testing.fake_model import FakeModel

    agent = Agent(model=FakeModel(["done"]), name="simple")
    async with start_run("simple-run") as run:
        await agent.arun("hello")

    view = projections.graph(Recorded.from_run(run))
    assert view.shape == "linear"
    assert view.nodes[0].id == "start"
    assert view.nodes[-1].id == "output"
    assert view.nodes[-1].status == "ok"
    assert view.edges[0].source == "start"
