"""AI dependency graphs, behavior manifests and drift detection."""

from __future__ import annotations

import pytest

from rewyn.agents.agent import Agent
from rewyn.core.manifest import (
    BehaviorManifest,
    DependencyGraph,
    DriftKind,
    ManifestError,
    detect_drift,
    detect_run_drift,
)
from rewyn.core.run import start_run
from rewyn.guardrails.validators import ProhibitedContentGuardrail
from rewyn.replay.recorder import RecordedRun
from rewyn.runtime.recorder import default_recorder
from rewyn.skills.skill import Skill
from rewyn.testing.fake_model import FakeModel
from rewyn.tools.tool import tool


@tool
def search(query: str) -> str:
    """Search the corpus."""
    return f"results for {query}"


def build_agent(*, version="1", instructions="Answer briefly.", answer="done", name="researcher"):
    skill = Skill(
        name="finance",
        description="Finance rules",
        instructions="Cite sources.",
        version="3",
    )
    return Agent(
        FakeModel([answer], name="fake-1"),
        tools=[search],
        name=name,
        version=version,
        instructions=instructions,
        skills=[skill],
        guardrails=[ProhibitedContentGuardrail(["forbidden"])],
    )


def record(**kwargs) -> str:
    run_id = build_agent(**kwargs).run("research entropy").run_id
    default_recorder().flush()
    return run_id


def test_dependency_graph_lists_everything_a_run_used():
    graph = DependencyGraph.from_runs([record()])
    kinds = {n.kind for n in graph.nodes}
    assert {"agent", "model", "tool", "skill"} <= kinds
    assert graph.of_kind("model")[0].name == "fake:fake-1"
    assert graph.fingerprint().startswith("sha256:")


def test_dependency_graph_renders_the_spec_tree():
    tree = DependencyGraph.from_runs([record()]).render()
    lines = tree.splitlines()
    assert lines[0].startswith("Agent researcher")
    assert any("model: fake:fake-1" in line for line in lines)
    assert any("skill: finance (v3)" in line for line in lines)
    assert lines[-1].startswith("└──")


def test_behavior_manifest_sections_follow_the_spec_fields():
    manifest = BehaviorManifest.from_runs("support-agent", [record()], version="1.4.2")
    assert manifest.application == "support-agent"
    assert manifest.version == "1.4.2"
    assert [m.name for m in manifest.models] == ["fake:fake-1"]
    assert [s.name for s in manifest.skills] == ["finance"]
    assert "search" in [t.name for t in manifest.tools]
    assert [g.name for g in manifest.guardrails] == ["prohibited_content"]
    assert [p.name for p in manifest.prompts] == ["system"]
    assert manifest.fingerprint().startswith("sha256:")


def test_manifest_round_trips_through_storage():
    manifest = BehaviorManifest.from_runs("support-agent", [record()], version="1.0.0")
    path = manifest.save()
    loaded = BehaviorManifest.load(path)
    assert loaded.fingerprint() == manifest.fingerprint()
    assert BehaviorManifest.load("support-agent-1.0.0").version == "1.0.0"


def test_loading_a_missing_manifest_is_an_error():
    with pytest.raises(ManifestError, match="not found"):
        BehaviorManifest.load("no-such-app")


def test_identical_manifests_show_no_drift():
    run_id = record()
    first = BehaviorManifest.from_runs("app", [run_id], version="1")
    second = BehaviorManifest.from_runs("app", [run_id], version="1")
    report = detect_drift(first, second)
    assert not report.drifted
    assert "no dependency drift" in report.render()


def test_a_changed_prompt_is_detected_as_content_drift():
    before = BehaviorManifest.from_runs("app", [record()], version="1")
    after = BehaviorManifest.from_runs(
        "app", [record(instructions="Answer in great detail with citations.")], version="2"
    )
    report = detect_drift(before, after)
    prompt_findings = report.of_kind("prompt")
    assert prompt_findings
    assert prompt_findings[0].kind is DriftKind.CONTENT_CHANGED
    assert prompt_findings[0].likely_cause == "prompt changed"


def test_a_new_agent_version_is_detected_as_version_drift():
    before = BehaviorManifest.from_runs("app", [record(version="1")], version="1")
    after = BehaviorManifest.from_runs("app", [record(version="2")], version="2")
    findings = detect_drift(before, after).of_kind("agent")
    assert findings[0].kind is DriftKind.VERSION_CHANGED
    assert findings[0].before is not None


def test_a_removed_tool_is_detected():
    before = BehaviorManifest.from_runs("app", [record()], version="1")
    bare = Agent(FakeModel(["done"], name="fake-1"), name="researcher")
    with start_run("bare", record=False) as run:
        bare.run("research entropy")
    after = BehaviorManifest.from_runs("app", [RecordedRun.from_run(run)], version="2")
    findings = {f.dependency_kind: f for f in detect_drift(before, after).findings}
    assert findings["tool"].kind is DriftKind.REMOVED


def test_silent_drift_is_named_rather_than_explained_away():
    first = record(answer="entropy is disorder")
    second = record(answer="entropy measures uncertainty")
    report = detect_run_drift(first, second)
    assert report.behavior_changed is True
    assert report.silent_drift
    finding = report.findings[0]
    assert finding.kind is DriftKind.UNEXPLAINED
    assert "provider update" in finding.likely_cause


def test_identical_runs_are_not_flagged_as_drift():
    report = detect_run_drift(record(answer="same"), record(answer="same"))
    assert report.behavior_changed is False
    assert not report.drifted


def test_drift_between_runs_attributes_a_dependency_change():
    report = detect_run_drift(
        record(answer="same"), record(answer="same", instructions="A different system prompt.")
    )
    assert report.drifted
    assert not report.silent_drift
    assert report.of_kind("prompt")


def test_a_dependency_graph_falls_back_to_the_run_name_without_an_agent():
    from rewyn.core.run import start_run

    with start_run("bare-model-call", record=False) as run:
        run.add_dependency(FakeModel([], name="m").dependency)
    graph = DependencyGraph.from_runs([run])
    assert graph.root == "bare-model-call"
    assert graph.render().splitlines()[0] == "bare-model-call"


def test_dependency_graphs_accept_run_objects_and_manifests():
    from rewyn.core.run import start_run

    with start_run("direct", record=False) as run:
        run.add_dependency(FakeModel([], name="m").dependency)
    from_run = DependencyGraph.from_runs([run])
    from_manifest = DependencyGraph.from_manifests([run.manifest])
    assert from_run.fingerprint() == from_manifest.fingerprint()


def test_a_run_manifest_cannot_be_read_from_an_arbitrary_object():
    with pytest.raises(ManifestError, match="cannot read a run manifest"):
        DependencyGraph.from_runs([object()])


def test_manifest_entries_are_reachable_by_kind_and_name():
    manifest = BehaviorManifest.from_runs("app", [record()], version="1")
    assert manifest.get("model", "fake:fake-1") is not None
    assert manifest.get("model", "nope") is None
    assert manifest.get("no-such-kind", "x") is None


def test_dependency_kinds_with_no_manifest_section_are_skipped():
    from rewyn.core.run import DependencyRef, start_run

    with start_run("odd", record=False) as run:
        run.add_dependency(DependencyRef(kind="mystery", name="thing"))
        run.add_dependency(FakeModel([], name="m").dependency)
    manifest = BehaviorManifest.from_runs("app", [run], version="1")
    assert [e.name for e in manifest.entries()] == ["fake:m"]


def test_drift_findings_describe_a_change_with_its_likely_cause():
    before = BehaviorManifest.from_runs("app", [record(version="1")], version="1")
    after = BehaviorManifest.from_runs("app", [record(version="2")], version="2")
    finding = detect_drift(before, after).of_kind("agent")[0]
    description = finding.describe()
    assert description.startswith("agent:researcher ")
    assert "->" in description
    assert "agent configuration changed" in description


def test_manifests_cannot_be_built_from_arbitrary_objects():
    with pytest.raises(ManifestError):
        BehaviorManifest.from_runs("app", [object()])
