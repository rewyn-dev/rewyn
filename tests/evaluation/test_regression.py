"""Regression testing and release gates."""

from __future__ import annotations

import pytest

from rewyn.agents.agent import Agent
from rewyn.core.run import RunStatus
from rewyn.evaluation.dataset import Dataset
from rewyn.evaluation.metrics import exact_match, no_errors, not_empty
from rewyn.evaluation.regression import (
    RegressionError,
    RegressionReport,
    ReleaseGate,
    Thresholds,
    arun_regression,
    run_regression,
)
from rewyn.testing.fake_model import FakeModel


def dataset(name: str = "qa-critical") -> Dataset:
    data = Dataset(name=name)
    data.add("capital of France?", "Paris", tags=["geography"])
    data.add("capital of Japan?", "Tokyo", tags=["geography"])
    return data


def perfect_agent() -> Agent:
    answers = {"capital of France?": "Paris", "capital of Japan?": "Tokyo"}
    return Agent(
        FakeModel([lambda request: answers.get(request.messages[-1].text, "?")], cycle=True),
        name="qa",
    )


def wrong_agent() -> Agent:
    return Agent(FakeModel(["Lyon"], cycle=True), name="qa")


def test_a_passing_regression_run():
    report = run_regression(dataset(), perfect_agent(), evaluators=[exact_match(), not_empty()])
    assert report.total == 2
    assert report.success_rate == 1.0
    assert report.passed
    assert report.dataset == "qa-critical"
    assert report.dataset_fingerprint.startswith("sha256:")
    assert report.target == "qa"


def test_each_case_runs_in_its_own_recorded_run():
    report = run_regression(dataset(), perfect_agent(), evaluators=[not_empty()])
    run_ids = {c.run_id for c in report.cases}
    assert len(run_ids) == 2
    assert all(c.run_id is not None for c in report.cases)


def test_a_failing_case_is_reported_with_its_reason():
    report = run_regression(dataset(), wrong_agent(), evaluators=[exact_match()])
    assert not report.passed
    assert report.success_rate == 0.0
    assert len(report.failures) == 2
    assert "expected" in report.failures[0].scores[0].reason


def test_metrics_are_aggregated_per_evaluator():
    report = run_regression(dataset(), perfect_agent(), evaluators=[exact_match(), not_empty()])
    names = {m.name for m in report.metrics}
    assert names == {"exact_match", "not_empty"}
    assert report.metric("exact_match").mean == 1.0
    assert report.metric("exact_match").pass_rate == 1.0


def test_a_target_that_raises_becomes_a_failed_case():
    def exploding(_payload):
        raise ValueError("target is broken")

    report = run_regression(dataset(), exploding, evaluators=[no_errors()])
    assert len(report.errors) == 2
    assert "target is broken" in report.errors[0].error
    assert not report.passed


def test_an_empty_dataset_is_refused():
    with pytest.raises(RegressionError, match="no items"):
        run_regression(Dataset(name="empty"), perfect_agent())


def test_reports_persist_and_can_be_used_as_a_baseline():
    baseline = run_regression(dataset(), wrong_agent(), evaluators=[exact_match()])
    assert RegressionReport.load(baseline.id).id == baseline.id

    improved = run_regression(
        dataset(), perfect_agent(), evaluators=[exact_match()], baseline="latest"
    )
    assert improved.baseline_id == baseline.id
    assert improved.baseline_success_rate == 0.0
    assert improved.success_delta() == 1.0


def test_history_is_newest_first_and_scoped_to_one_dataset():
    run_regression(dataset("a"), perfect_agent(), evaluators=[not_empty()])
    run_regression(dataset("b"), perfect_agent(), evaluators=[not_empty()])
    assert {r.dataset for r in RegressionReport.history("a")} == {"a"}
    assert RegressionReport.latest("b").dataset == "b"
    assert RegressionReport.latest("missing") is None


def test_thresholds_become_gates():
    report = run_regression(
        dataset(),
        wrong_agent(),
        evaluators=[exact_match()],
        thresholds=Thresholds(min_success_rate=0.9, max_cost_per_run=1.0),
    )
    assert not report.passed
    names = {g.name for g in report.gates}
    assert names == {"success_rate", "cost_per_run"}
    assert not report.gates[0].passed


def test_a_gate_on_a_metric_mean():
    report = run_regression(
        dataset(),
        perfect_agent(),
        evaluators=[exact_match()],
        thresholds=Thresholds(min_metric={"exact_match": 0.9}),
    )
    assert report.passed
    assert report.gates[0].name == "metric:exact_match"


def test_a_missing_metric_fails_its_gate_rather_than_being_ignored():
    report = run_regression(
        dataset(),
        perfect_agent(),
        evaluators=[exact_match()],
        thresholds=Thresholds(min_metric={"groundedness": 0.8}),
    )
    gate = report.gates[0]
    assert not gate.passed
    assert "not produced" in gate.detail


def test_a_ceiling_on_a_missing_metric_also_fails():
    report = run_regression(
        dataset(),
        perfect_agent(),
        evaluators=[exact_match()],
        thresholds=Thresholds(max_metric={"hallucination": 0.05}),
    )
    gate = report.gates[0]
    assert not gate.passed
    assert "not produced" in gate.detail


def test_a_success_regression_against_a_baseline_fails_the_gate():
    run_regression(dataset(), perfect_agent(), evaluators=[exact_match()])
    report = run_regression(
        dataset(),
        wrong_agent(),
        evaluators=[exact_match()],
        thresholds=Thresholds(max_success_regression=0.05),
        baseline="latest",
    )
    assert not report.passed
    gate = next(g for g in report.gates if g.name == "success_regression")
    assert gate.actual == 1.0


def test_total_cost_and_latency_gates():
    report = run_regression(
        dataset(),
        perfect_agent(),
        evaluators=[exact_match()],
        thresholds=Thresholds(max_total_cost=10.0, max_avg_latency_ms=60_000),
    )
    assert report.passed
    assert {g.name for g in report.gates} == {"total_cost", "avg_latency_ms"}

    tight = run_regression(
        dataset(),
        perfect_agent(),
        evaluators=[exact_match()],
        thresholds=Thresholds(max_total_cost=0.0, max_avg_latency_ms=0.0),
    )
    assert not tight.passed
    assert all(not g.passed for g in tight.gates)


def test_a_cost_increase_against_a_baseline_fails_the_gate():
    baseline = run_regression(dataset(), perfect_agent(), evaluators=[exact_match()], save=False)
    baseline.cases[0].cost = 0.0
    baseline.cases[1].cost = 0.0
    report = run_regression(
        dataset(),
        perfect_agent(),
        evaluators=[exact_match()],
        thresholds=Thresholds(max_cost_increase=0.0),
        baseline=baseline,
    )
    gate = next(g for g in report.gates if g.name == "cost_increase")
    assert not gate.passed
    assert "baseline $0.000000/run" in gate.detail


def test_a_baseline_can_be_named_by_report_id():
    first = run_regression(dataset(), wrong_agent(), evaluators=[exact_match()])
    second = run_regression(
        dataset(), perfect_agent(), evaluators=[exact_match()], baseline=first.id
    )
    assert second.baseline_id == first.id


def test_loading_a_missing_report_is_an_error():
    with pytest.raises(RegressionError, match="not found"):
        RegressionReport.load("report_missing")


def test_history_of_an_untouched_home_is_empty():
    assert RegressionReport.history() == []


async def test_an_async_callable_target_is_awaited():
    async def target(payload):
        return "Paris" if "France" in payload else "Tokyo"

    report = await arun_regression(dataset(), target, evaluators=[exact_match()])
    assert report.success_rate == 1.0
    assert report.target == "target"


async def test_a_target_returning_an_object_is_serialised_for_scoring():
    async def target(_payload):
        return {"answer": "Paris"}

    report = await arun_regression(dataset(), target, evaluators=[not_empty()])
    assert report.cases[0].output == '{"answer": "Paris"}'


def test_an_unrunnable_target_is_refused():
    report = run_regression(dataset(), object(), evaluators=[not_empty()])
    assert len(report.errors) == 2
    assert "neither runnable nor callable" in report.errors[0].error


def test_an_evaluator_whose_scores_carry_another_name_is_left_out_of_the_metrics():
    from rewyn.evaluation.evaluator import Evaluator, Score

    def renaming(_subject):
        return Score(evaluator="something_else", value=1.0, passed=True)

    report = run_regression(
        dataset(),
        perfect_agent(),
        evaluators=[exact_match(), Evaluator(renaming, name="renaming")],
        save=False,
    )
    assert report.metric("renaming") is None
    assert {m.name for m in report.metrics} == {"exact_match"}
    assert report.cases[0].score_for("something_else") is not None


def test_case_scores_can_be_looked_up_by_evaluator_name():
    report = run_regression(dataset(), perfect_agent(), evaluators=[exact_match()], save=False)
    case = report.cases[0]
    assert case.score_for("exact_match").passed
    assert case.score_for("nope") is None


def test_the_rendered_report_counts_errors():
    def exploding(_payload):
        raise ValueError("broken")

    report = run_regression(dataset(), exploding, evaluators=[no_errors()], save=False)
    assert "Errors: 2" in report.render()


def test_a_ceiling_on_a_produced_metric_is_checked_against_its_mean():
    report = run_regression(
        dataset(),
        wrong_agent(),
        evaluators=[exact_match()],
        thresholds=Thresholds(max_metric={"exact_match": 0.5}),
    )
    gate = report.gates[0]
    assert gate.passed
    assert gate.actual == 0.0


def test_a_corrupt_report_file_is_skipped_by_the_history(rewyn_home):
    run_regression(dataset(), perfect_agent(), evaluators=[exact_match()])
    (RegressionReport.directory() / "report_broken.json").write_text("{not json", encoding="utf-8")
    assert len(RegressionReport.history("qa-critical")) == 1


def test_a_case_whose_run_failed_without_raising_records_the_run_error():
    from rewyn.core.run import current_run

    def failing(_payload):
        run = current_run()
        assert run is not None
        run.finish(status=RunStatus.FAILED, error="downstream refused")
        return "partial"

    report = run_regression(dataset(), failing, evaluators=[no_errors()], save=False)
    assert report.cases[0].error == "downstream refused"
    assert not report.passed


def test_release_gate_repr():
    assert repr(ReleaseGate("pre-deploy", Thresholds())) == "ReleaseGate('pre-deploy')"


def test_release_gate_applies_thresholds_to_an_existing_report():
    report = run_regression(dataset(), perfect_agent(), evaluators=[exact_match()], save=False)
    gate = ReleaseGate("pre-deploy", Thresholds(min_success_rate=0.95))
    gated = gate.evaluate(report)
    assert gated.passed
    assert gated.metadata["gate"] == "pre-deploy"
    assert gate.fingerprint().startswith("sha256:")

    strict = ReleaseGate("impossible", Thresholds(min_success_rate=1.1))
    assert not strict.evaluate(report).passed


def test_the_rendered_report_follows_the_spec_shape():
    run_regression(dataset(), wrong_agent(), evaluators=[exact_match()])
    report = run_regression(
        dataset(), perfect_agent(), evaluators=[exact_match()], baseline="latest"
    )
    text = report.render()
    assert "Dataset: qa-critical" in text
    assert "Tests: 2" in text
    assert "0.0% → 100.0%" in text
    assert text.strip().endswith("PASS")


def test_summary_is_json_serialisable():
    report = run_regression(dataset(), perfect_agent(), evaluators=[not_empty()])
    summary = report.summary()
    assert summary["tests"] == 2
    assert summary["passed"] is True
    assert summary["success_delta"] is None


async def test_cases_can_run_concurrently():
    report = await arun_regression(
        dataset(), perfect_agent(), evaluators=[exact_match()], concurrency=2
    )
    assert report.success_rate == 1.0
    assert len({c.run_id for c in report.cases}) == 2


def test_the_dataset_is_recorded_as_a_dependency_of_each_case():
    from rewyn.replay.recorder import RecordedRun

    report = run_regression(dataset(), perfect_agent(), evaluators=[not_empty()])
    from rewyn.runtime.recorder import default_recorder

    default_recorder().flush()
    recorded = RecordedRun.load(report.cases[0].run_id)
    kinds = {d.kind for d in recorded.manifest.dependencies}
    assert "dataset" in kinds
