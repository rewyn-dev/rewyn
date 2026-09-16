from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rewyn import Agent, __version__
from rewyn.cli.main import app
from rewyn.runtime.recorder import default_recorder
from rewyn.testing import FakeModel

runner = CliRunner()


def _recorded_run() -> str:
    result = Agent(model=FakeModel(["hello"]), name="cli-demo").run("hi")
    default_recorder().flush()
    return result.run_id


def test_version_and_help() -> None:
    assert runner.invoke(app, ["--version"]).output.strip() == f"rewyn {__version__}"
    assert "Build. Run. Replay." in runner.invoke(app, ["--help"]).output


def test_init_creates_home(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path / "proj")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "proj" / ".rewyn" / "config.json").exists()


def test_runs_and_inspect(rewyn_home: Path) -> None:
    assert "no runs recorded" in runner.invoke(app, ["runs"]).output
    run_id = _recorded_run()
    listing = runner.invoke(app, ["runs"])
    assert run_id in listing.output
    assert "cli-demo" in listing.output
    as_json = json.loads(runner.invoke(app, ["runs", "--json"]).output)
    assert as_json[0]["id"] == run_id

    inspected = runner.invoke(app, ["inspect", "latest"])
    assert inspected.exit_code == 0, inspected.output
    assert f"run       {run_id}" in inspected.output
    assert "MODEL_CALLED" in inspected.output
    assert "dependencies" in inspected.output
    prefix = runner.invoke(app, ["inspect", run_id[:12], "--no-events", "--json"])
    assert json.loads(prefix.output)["manifest"]["id"] == run_id
    missing = runner.invoke(app, ["inspect", "run_nope"])
    assert missing.exit_code == 1


def test_export_and_import(tmp_path: Path) -> None:
    run_id = _recorded_run()
    exported = runner.invoke(app, ["export", run_id, "--to", str(tmp_path), "--zip"])
    assert exported.exit_code == 0, exported.output
    zip_path = tmp_path / f"{run_id}.zip"
    assert zip_path.exists()
    dup = runner.invoke(app, ["import", str(zip_path)])
    assert dup.exit_code == 1
    assert "already exists" in dup.output
    ok = runner.invoke(app, ["import", str(zip_path), "--overwrite"])
    assert ok.exit_code == 0, ok.output
    assert run_id in ok.output


def test_doctor_reports_environment() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "python" in result.output
    assert "anthropic" in result.output
    assert "all checks passed" in result.output


def test_doctor_survives_a_provider_extra_that_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # find_spec imports the parent of a dotted name, so a missing "google"
    # raises instead of returning None. The workspace installs every extra, so
    # only a bare `pip install rewyn` ever saw it -- which is most of them.
    import importlib.util

    real = importlib.util.find_spec

    def absent(name: str, package: str | None = None):
        if name.startswith("google"):
            raise ModuleNotFoundError(f"No module named {name.split('.', maxsplit=1)[0]!r}")
        return real(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", absent)
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "google.genai" in result.output
    assert "not installed" in result.output


def test_skills_commands(tmp_path: Path) -> None:
    root = tmp_path / "skills" / "demo"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\ndescription: Demo skill\nversion: '3'\n---\nDo demo.")
    empty = runner.invoke(app, ["skills", "--path", str(tmp_path / "none")])
    assert "no skills found" in empty.output
    listed = runner.invoke(app, ["skills", "--path", str(tmp_path / "skills")])
    assert listed.exit_code == 0, listed.output
    assert "demo" in listed.output
    assert "v3" in listed.output
    shown = runner.invoke(app, ["skills", "show", "demo", "--path", str(tmp_path / "skills")])
    assert "Do demo." in shown.output
    by_path = runner.invoke(app, ["skills", "show", str(root)])
    assert "fingerprint  sha256:" in by_path.output
    missing = runner.invoke(app, ["skills", "show", "nope", "--path", str(tmp_path / "skills")])
    assert missing.exit_code == 1


def test_mcp_commands(tmp_path: Path) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {"mcpServers": {"gh": {"command": "npx", "args": ["gh"]}, "crm": {"url": "http://x"}}}
        )
    )
    none = runner.invoke(app, ["mcp", "--config", str(tmp_path / "missing.json")])
    assert "no MCP servers configured" in none.output
    listed = runner.invoke(app, ["mcp", "--config", str(config)])
    assert listed.exit_code == 0, listed.output
    assert "gh" in listed.output
    assert "npx gh" in listed.output
    assert "http://x" in listed.output
    unknown = runner.invoke(app, ["mcp", "tools", "nope", "--config", str(config)])
    assert unknown.exit_code == 1


def test_agents_command() -> None:
    assert "no agent runs" in runner.invoke(app, ["agents"]).output
    run_id = _recorded_run()
    listed = runner.invoke(app, ["agents"])
    assert listed.exit_code == 0, listed.output
    assert "cli-demo" in listed.output
    one = runner.invoke(app, ["agents", "cli-demo"])
    assert run_id in one.output
    assert "no runs for agent" in runner.invoke(app, ["agents", "ghost"]).output


def test_a_skill_name_is_not_mistaken_for_a_local_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `demo/` folder in the cwd must not shadow the skill called "demo"."""
    root = tmp_path / "skills" / "demo"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\ndescription: Demo skill\n---\nDo demo.")
    (tmp_path / "demo").mkdir()  # an unrelated directory of the same name
    monkeypatch.chdir(tmp_path)

    shown = runner.invoke(app, ["skills", "show", "demo", "--path", str(tmp_path / "skills")])
    assert shown.exit_code == 0, shown.output
    assert "Do demo." in shown.output


def test_an_explicit_skill_directory_still_loads(tmp_path: Path) -> None:
    root = tmp_path / "policies" / "refunds"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\ndescription: Refunds\n---\nRefund within 30 days.")
    shown = runner.invoke(app, ["skills", "show", str(root)])
    assert shown.exit_code == 0, shown.output
    assert "Refund within 30 days." in shown.output
