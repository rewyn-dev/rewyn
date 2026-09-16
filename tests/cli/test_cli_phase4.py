"""CLI: replay, diff, eval, test, datasets, manifest and drift."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from rewyn import Agent
from rewyn.cli.main import app
from rewyn.runtime.recorder import default_recorder
from rewyn.testing import FakeModel
from rewyn.tools.tool import tool

runner = CliRunner()


@tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def record(answer: str = "hello", question: str = "hi", name: str = "cli-demo") -> str:
    result = Agent(model=FakeModel([answer]), name=name).run(question)
    default_recorder().flush()
    return result.run_id


def write_module(tmp_path: Path) -> Path:
    """A module the CLI can load an agent and an evaluator out of."""
    module = tmp_path / "fixtures_cli.py"
    module.write_text(
        "\n".join(
            [
                "from rewyn import Agent",
                "from rewyn.evaluation.evaluator import evaluator",
                "from rewyn.evaluation.metrics import exact_match",
                "from rewyn.testing import FakeModel",
                "",
                "agent = Agent(FakeModel(['hello'], cycle=True), name='cli-demo')",
                "",
                "@evaluator",
                "def says_hello(run):",
                "    return 'hello' in run.output",
                "",
                "panel = [says_hello]",
                "strict = [exact_match()]",
            ]
        ),
        encoding="utf-8",
    )
    return module


def test_replay_reconstructs_a_run() -> None:
    run_id = record()
    result = runner.invoke(app, ["replay", run_id])
    assert result.exit_code == 0, result.output
    assert "mode      reconstruct" in result.output
    assert "identical True" in result.output
    assert "faithful True" in result.output


def test_replay_json_output() -> None:
    run_id = record()
    payload = json.loads(runner.invoke(app, ["replay", run_id, "--json"]).output)
    assert payload["original_run_id"] == run_id
    assert payload["mode"] == "reconstruct"


def test_replay_of_a_missing_run_fails_cleanly() -> None:
    result = runner.invoke(app, ["replay", "run_missing"])
    assert result.exit_code == 1


def test_diff_reports_differences_and_explanations() -> None:
    first = record("entropy is disorder")
    second = record("entropy measures uncertainty")
    result = runner.invoke(app, ["diff", first, second])
    assert result.exit_code == 0, result.output
    assert "differences" in result.output
    assert "possible explanations" in result.output
    assert "hypotheses, not facts" in result.output


def test_diff_can_be_limited_to_a_dimension() -> None:
    first = record("a")
    second = record("b")
    result = runner.invoke(app, ["diff", first, second, "-d", "output"])
    assert result.exit_code == 0, result.output
    assert "output.output" in result.output
    assert "cost." not in result.output


def test_diff_rejects_an_unknown_dimension() -> None:
    first = record("a")
    result = runner.invoke(app, ["diff", first, first, "-d", "vibes"])
    assert result.exit_code == 1
    assert "valid dimensions" in result.output


def test_diff_of_a_run_against_itself_is_clean() -> None:
    run_id = record()
    assert "no differences" in runner.invoke(app, ["diff", run_id, run_id]).output


def test_eval_with_builtin_metrics() -> None:
    run_id = record()
    result = runner.invoke(app, ["eval", run_id])
    assert result.exit_code == 0, result.output
    assert "no_errors" in result.output
    assert "PASS" in result.output


def test_eval_exits_nonzero_when_an_expectation_fails() -> None:
    run_id = record("goodbye")
    result = runner.invoke(app, ["eval", run_id, "--expect", "hello"])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_eval_loads_an_evaluator_from_a_module(tmp_path: Path) -> None:
    module = write_module(tmp_path)
    run_id = record()
    result = runner.invoke(app, ["eval", run_id, "-e", f"{module}:says_hello"])
    assert result.exit_code == 0, result.output
    assert "says_hello" in result.output


def test_eval_rejects_a_reference_without_an_attribute() -> None:
    run_id = record()
    result = runner.invoke(app, ["eval", run_id, "-e", "rewyn.evaluation"])
    assert result.exit_code == 1
    assert "module:attribute" in result.output


def test_datasets_lifecycle(tmp_path: Path) -> None:
    assert "no datasets yet" in runner.invoke(app, ["datasets"]).output
    run_id = record()
    added = runner.invoke(app, ["datasets", "add", "qa", "--run", run_id])
    assert added.exit_code == 0, added.output
    assert "now has 1 item" in added.output

    listing = runner.invoke(app, ["datasets"])
    assert "qa" in listing.output

    shown = runner.invoke(app, ["datasets", "show", "qa"])
    assert "fingerprint sha256:" in shown.output
    assert "expected hello" in shown.output


def test_datasets_show_of_a_missing_dataset_fails() -> None:
    assert runner.invoke(app, ["datasets", "show", "nope"]).exit_code == 1


def test_test_command_runs_a_dataset_and_gates(tmp_path: Path) -> None:
    module = write_module(tmp_path)
    run_id = record()
    runner.invoke(app, ["datasets", "add", "qa", "--run", run_id])

    result = runner.invoke(
        app,
        ["test", "qa", "--target", f"{module}:agent", "-e", f"{module}:panel"],
    )
    assert result.exit_code == 0, result.output
    assert "Dataset: qa" in result.output
    assert "Tests: 1" in result.output
    assert result.output.rstrip().splitlines()[-1].startswith("report ")


def test_test_command_fails_the_gate_below_the_success_threshold(tmp_path: Path) -> None:
    module = write_module(tmp_path)
    run_id = record("goodbye")
    runner.invoke(app, ["datasets", "add", "qa2", "--run", run_id])
    result = runner.invoke(
        app,
        [
            "test",
            "qa2",
            "--target",
            f"{module}:agent",
            "-e",
            f"{module}:strict",
            "--min-success",
            "1.0",
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_test_command_reports_a_missing_dataset() -> None:
    result = runner.invoke(app, ["test", "nope", "--target", "rewyn:Agent"])
    assert result.exit_code == 1


def test_manifest_and_dependency_graph() -> None:
    record()
    text = runner.invoke(app, ["manifest", "support-agent", "--version", "1.4.2"])
    assert text.exit_code == 0, text.output
    assert "support-agent 1.4.2" in text.output
    assert "models:" in text.output

    graph = runner.invoke(app, ["manifest", "support-agent", "--graph"])
    assert "model: fake:fake-1" in graph.output

    payload = json.loads(runner.invoke(app, ["manifest", "support-agent", "--json"]).output)
    assert payload["application"] == "support-agent"


def test_manifest_can_be_saved(rewyn_home: Path) -> None:
    record()
    result = runner.invoke(app, ["manifest", "app", "--version", "2.0.0", "--save"])
    assert result.exit_code == 0, result.output
    assert (rewyn_home / "manifests" / "app-2.0.0.json").exists()


def test_drift_between_two_runs_names_silent_drift() -> None:
    first = record("entropy is disorder")
    second = record("entropy measures uncertainty")
    result = runner.invoke(app, ["drift", first, second])
    assert result.exit_code == 0, result.output
    assert "behaviour changed with no dependency change" in result.output


def test_drift_between_saved_manifests(rewyn_home: Path) -> None:
    record()
    runner.invoke(app, ["manifest", "app", "--version", "1.0.0", "--save"])
    record(name="renamed-agent")
    runner.invoke(app, ["manifest", "app", "--version", "2.0.0", "--save"])
    result = runner.invoke(app, ["drift", "app-1.0.0", "app-2.0.0"])
    assert result.exit_code == 0, result.output
    assert "agent:" in result.output
