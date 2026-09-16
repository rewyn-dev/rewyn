from __future__ import annotations

from pathlib import Path

import pytest

from rewyn import Agent
from rewyn.core.event import EventType, ListSink
from rewyn.core.run import start_run
from rewyn.skills import (
    SkillLoadError,
    SkillManager,
    SkillRegistry,
    discover_skills,
    load_skill,
    parse_skill_markdown,
    skill,
)
from rewyn.testing import FakeModel

SKILL_MD = """---
name: financial-analysis
description: Analyse company financials and credit risk.
version: "2"
allowed-tools: [search, calculator]
permissions:
  - finance:read
license: MIT
author: finance-team
---
# Financial analysis

1. Pull the last 12 months of invoices.
2. Compute days-sales-outstanding.
"""


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    root = tmp_path / "skills" / "financial-analysis"
    (root / "scripts").mkdir(parents=True)
    (root / "resources").mkdir()
    (root / "SKILL.md").write_text(SKILL_MD)
    (root / "scripts" / "dso.py").write_text("print('dso')\n")
    (root / "resources" / "policy.md").write_text("Credit policy v3\n")
    return root


def test_parse_and_load_skill(skill_dir: Path) -> None:
    loaded = load_skill(skill_dir)
    assert loaded.name == "financial-analysis"
    assert loaded.version == "2"
    assert loaded.description == "Analyse company financials and credit risk."
    assert loaded.instructions.startswith("# Financial analysis")
    assert loaded.allowed_tools == ["search", "calculator"]
    assert loaded.permissions == ["finance:read"]
    assert loaded.license == "MIT"
    assert loaded.metadata == {"author": "finance-team"}
    assert loaded.scripts == ["scripts/dso.py"]
    assert loaded.resources == ["resources/policy.md"]
    assert loaded.resolve("resources/policy.md").read_text() == "Credit policy v3\n"
    with pytest.raises(PermissionError):
        loaded.resolve("../../outside")
    with pytest.raises(FileNotFoundError):
        loaded.resolve("resources/missing.md")
    assert load_skill(skill_dir / "SKILL.md").fingerprint() == loaded.fingerprint()
    assert loaded.dependency.kind == "skill"
    assert loaded.summary() == "- financial-analysis: Analyse company financials and credit risk."


def test_loader_errors(tmp_path: Path) -> None:
    assert parse_skill_markdown("no frontmatter") == ({}, "no frontmatter")
    with pytest.raises(SkillLoadError, match=r"no SKILL\.md"):
        load_skill(tmp_path)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: bad\n---\nbody")
    with pytest.raises(SkillLoadError, match="missing a description"):
        load_skill(bad)
    (bad / "SKILL.md").write_text("---\n- not: a mapping\n---\nbody")
    with pytest.raises(SkillLoadError, match="mapping"):
        load_skill(bad)


def test_discovery_and_registry(skill_dir: Path, tmp_path: Path) -> None:
    other = tmp_path / "skills" / "other"
    other.mkdir()
    (other / "SKILL.md").write_text("---\ndescription: Other skill\n---\nDo other things.")
    broken = tmp_path / "skills" / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("---\nname: broken\n---\n")
    errors: list[str] = []
    found = discover_skills([tmp_path / "skills", tmp_path / "missing"], errors=errors)
    assert sorted(s.name for s in found) == ["financial-analysis", "other"]
    assert len(errors) == 1
    assert "broken" in errors[0]
    registry = SkillRegistry(found)
    assert registry.names() == [s.name for s in found]
    assert "other" in registry
    with pytest.raises(KeyError):
        registry["nope"]
    assert registry.register(skill_dir, replace=True).name == "financial-analysis"


async def test_progressive_disclosure_events_and_tools(skill_dir: Path) -> None:
    sink = ListSink()
    manager = SkillManager([skill_dir, skill("tiny", "Tiny skill", "Be tiny.")])
    items = manager.context_items()
    assert len(items) == 1
    assert "financial-analysis: Analyse" in items[0].content
    assert "- tiny: Tiny skill" in items[0].content
    tools = {t.name: t for t in manager.tools()}
    assert set(tools) == {"load_skill", "read_skill_file"}
    async with start_run("t", sinks=[sink]) as run:
        manager.load(run)
        manager.load(run)  # idempotent
        text = await tools["load_skill"].ainvoke(name="financial-analysis")
        assert text.startswith("# Financial analysis")
        policy = await tools["read_skill_file"].ainvoke(
            skill="financial-analysis", path="resources/policy.md"
        )
        assert policy == "Credit policy v3\n"
    loaded = sink.of_type(EventType.SKILL_LOADED)
    assert [e.payload["skill"] for e in loaded] == ["financial-analysis", "tiny"]
    assert loaded[0].payload["mode"] == "progressive"
    activated = sink.of_type(EventType.SKILL_ACTIVATED)[0]
    assert activated.payload["first_activation"] is True
    assert {d.name for d in run.manifest.dependencies if d.kind == "skill"} == {
        "financial-analysis",
        "tiny",
    }
    items = manager.context_items()
    assert [i.title for i in items] == ["available skills", "skill: financial-analysis (v2)"]
    assert "- tiny" in items[0].content
    assert "financial-analysis:" not in items[0].content


def test_eager_mode_includes_full_instructions() -> None:
    manager = SkillManager([skill("a", "A", "Do A."), skill("b", "B", "Do B.")], mode="eager")
    with start_run("t", sinks=[]) as run:
        manager.load(run)
    items = manager.context_items()
    assert [i.content for i in items] == ["Do A.", "Do B."]
    assert [t.name for t in manager.tools()] == ["read_skill_file"]
    assert SkillManager([]).tools() == []
    assert SkillManager([]).context_items() == []


async def test_agent_uses_skills_progressively(skill_dir: Path) -> None:
    model = FakeModel(
        [
            FakeModel.tool_call("load_skill", {"name": "financial-analysis"}),
            "DSO computed: 31 days.",
        ]
    )
    sink = ListSink()
    agent = Agent(model=model, instructions="You are an analyst.", skills=[skill_dir])
    async with start_run("t", sinks=[sink]):
        result = await agent.arun("Compute DSO for Acme")
    assert result.output == "DSO computed: 31 days."
    first_system = model.requests[0].messages[0].text
    assert "## Instructions" in first_system
    assert "load_skill tool" in first_system
    assert "# Financial analysis" not in first_system
    assert "Pull the last 12 months" in result.messages[3].tool_results[0].content
    assert sink.of_type(EventType.SKILL_ACTIVATED)[0].payload["skill"] == "financial-analysis"
    assert sink.of_type(EventType.CONTEXT_ASSEMBLED)[0].payload["decision"]["by_kind"]["skills"] > 0
