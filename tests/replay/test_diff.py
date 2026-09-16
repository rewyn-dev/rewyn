"""Execution diff: observed differences and separate causal hypotheses."""

from __future__ import annotations

from rewyn.agents.agent import Agent
from rewyn.replay.diff import ChangeKind, Dimension, diff, diff_runs, explain
from rewyn.replay.recorder import RecordedRun
from rewyn.runtime.recorder import default_recorder
from rewyn.testing.fake_model import FakeModel
from rewyn.tools.tool import tool


@tool
def lookup(term: str) -> str:
    """Look a term up."""
    return f"definition of {term}"


def run_agent(script, *, name="researcher", tools=(lookup,), model_name="fake-1") -> str:
    agent = Agent(FakeModel(script, name=model_name), tools=list(tools), name=name)
    result = agent.run("define entropy")
    default_recorder().flush()
    return result.run_id


def test_a_run_does_not_differ_from_itself():
    run_id = run_agent(["entropy is disorder"])
    recorded = RecordedRun.load(run_id)
    report = diff_runs(recorded, recorded)
    assert report.identical
    assert report.explanations == []


def test_output_and_cost_differences_are_reported_separately_from_causes():
    first = run_agent(["entropy is disorder"])
    second = run_agent(["entropy measures uncertainty in a system"], model_name="fake-2")
    report = diff(first, second)

    assert Dimension.OUTPUT in report.dimensions
    assert Dimension.MODEL in report.dimensions
    output_change = report.of(Dimension.OUTPUT)[0]
    assert output_change.kind is ChangeKind.CHANGED
    assert output_change.before == "entropy is disorder"

    causes = {e.cause for e in report.explanations if e.observed is Dimension.OUTPUT}
    assert Dimension.MODEL in causes
    assert all(0.0 <= e.confidence <= 1.0 for e in report.explanations)


def test_tool_trajectory_differences_are_captured():
    with_tool = run_agent([FakeModel.tool_call("lookup", {"term": "entropy"}), "done"])
    without_tool = run_agent(["done"])
    report = diff(with_tool, without_tool)
    tool_fields = {d.field for d in report.of(Dimension.TOOLS)}
    assert "sequence" in tool_fields or any(f.startswith("results") for f in tool_fields)
    assert report.of(Dimension.TOOLS)


def test_differences_can_be_limited_to_one_dimension():
    first = run_agent(["a"])
    second = run_agent(["b"])
    report = diff(first, second, dimensions=[Dimension.OUTPUT])
    assert report.dimensions == [Dimension.OUTPUT]


def test_an_outcome_change_with_no_input_change_is_left_unattributed():
    from rewyn.replay.diff import Difference

    differences = [
        Difference(
            dimension=Dimension.OUTPUT,
            field="output",
            kind=ChangeKind.CHANGED,
            before="a",
            after="b",
        )
    ]
    explanations = explain(differences)
    assert len(explanations) == 1
    assert explanations[0].cause is None
    assert "nondeterminism" in explanations[0].rationale


def test_added_and_removed_differences_describe_themselves():
    from rewyn.replay.diff import Difference

    added = Difference(dimension=Dimension.TOOLS, field="new", kind=ChangeKind.ADDED, after="v1")
    removed = Difference(
        dimension=Dimension.TOOLS, field="old", kind=ChangeKind.REMOVED, before="v1"
    )
    assert added.describe() == "tools.new: added 'v1'"
    assert removed.describe() == "tools.old: removed 'v1'"


def test_state_updates_are_compared_by_the_keys_they_touched():
    from rewyn.core.event import EventType
    from rewyn.core.run import start_run
    from rewyn.replay.diff import diff_runs

    def with_keys(keys):
        with start_run("stateful", record=False) as run:
            run.emit(EventType.STATE_UPDATED, {"keys": keys})
        return RecordedRun.from_run(run)

    report = diff_runs(with_keys(["answer"]), with_keys(["answer", "citations"]))
    state = report.of(Dimension.STATE)
    assert state
    assert any("keys" in d.field for d in state)


def test_an_attributed_explanation_names_its_cause():
    from rewyn.replay.diff import Explanation

    explanation = Explanation(
        observed=Dimension.OUTPUT,
        cause=Dimension.MODEL,
        confidence=0.8,
        rationale="a different model produces different text",
    )
    text = explanation.describe()
    assert text.startswith("output may have changed because model changed")
    assert "80% confidence" in text


def test_a_boolean_change_carries_no_numeric_delta():
    from rewyn.replay.diff import _compare

    differences = _compare(Dimension.OUTPUT, {"flag": True}, {"flag": False})
    assert differences[0].delta is None
    assert differences[0].before is True


def test_diff_summary_is_json_serialisable():
    first = run_agent(["a"])
    second = run_agent(["b"])
    summary = diff(first, second).summary()
    assert summary["run_a"] == first
    assert summary["identical"] is False
    assert isinstance(summary["dimensions"], list)
