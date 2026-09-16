"""Evaluators, deterministic metrics and LLM judges."""

from __future__ import annotations

import pytest

from rewyn.agents.agent import Agent
from rewyn.core.event import EventType
from rewyn.core.run import start_run
from rewyn.evaluation.evaluator import (
    EvaluationError,
    Evaluator,
    Score,
    Subject,
    aevaluate,
    as_evaluator,
    coerce_subject,
    evaluate,
    evaluator,
)
from rewyn.evaluation.judges import Criterion, LLMJudge, Verdict, judge_panel
from rewyn.evaluation.metrics import (
    contains,
    exact_match,
    json_valid,
    max_cost,
    max_latency,
    no_errors,
    not_empty,
    regex_match,
    schema_valid,
    tool_called,
    tool_correctness,
)
from rewyn.replay.recorder import RecordedRun
from rewyn.testing.fake_model import FakeModel
from rewyn.tools.tool import tool


@tool
def search(query: str) -> str:
    """Search the corpus."""
    return f"results for {query}"


def test_bare_decorator_builds_an_evaluator():
    @evaluator
    def business_success(run):
        """Did the agent do the thing the business cares about?"""
        return "refund issued" in run.output

    assert isinstance(business_success, Evaluator)
    assert business_success.name == "business_success"
    assert business_success.description.startswith("Did the agent")
    assert business_success.score(Subject(output="refund issued today")).passed
    assert not business_success.score(Subject(output="no")).passed


def test_decorator_accepts_options():
    @evaluator(name="quality", threshold=0.8, weight=2.0)
    def scored(run):
        return 0.9

    score = scored.score(Subject(output="x"))
    assert score.evaluator == "quality"
    assert score.passed
    assert score.weight == 2.0
    assert score.threshold == 0.8


def test_a_float_below_the_threshold_fails():
    @evaluator(threshold=0.8)
    def scored(run):
        return 0.5

    assert not scored.score(Subject(output="x")).passed


def test_tuple_return_carries_a_reason():
    @evaluator
    def scored(run):
        return False, "missing the citation"

    score = scored.score(Subject(output="x"))
    assert score.reason == "missing the citation"


def test_an_evaluator_that_raises_becomes_a_failing_score():
    @evaluator
    def broken(run):
        raise RuntimeError("evaluator bug")

    score = broken.score(Subject(output="x"))
    assert not score.passed
    assert score.error is not None
    assert "evaluator bug" in score.error


async def test_async_evaluators_are_awaited():
    @evaluator
    async def slow(run):
        return True

    assert (await slow.ascore(Subject(output="x"))).passed


def test_scoring_emits_an_event():
    @evaluator
    def always(run):
        return True

    with start_run("evaluation", record=False) as run:
        always.score(Subject(output="x"))
    scored = run.events_of(EventType.EVALUATION_SCORED)
    assert len(scored) == 1
    assert scored[0].payload["evaluator"] == "always"
    assert scored[0].payload["passed"] is True


def test_evaluators_are_versioned_and_fingerprinted():
    @evaluator
    def one(run):
        return True

    @evaluator
    def two(run):
        return False

    assert one.fingerprint() != two.fingerprint()
    assert one.dependency.kind == "evaluator"


def test_exact_match_uses_the_dataset_expectation():
    metric = exact_match()
    assert metric.score(Subject(output="Paris", expected="paris")).passed
    assert not metric.score(Subject(output="Lyon", expected="paris")).passed


def test_contains_and_regex():
    assert contains("refund").score(Subject(output="A refund was issued")).passed
    assert regex_match(r"\d{4}").score(Subject(output="order 2024 shipped")).passed
    assert not regex_match(r"^\d+$", full=True).score(Subject(output="abc")).passed


def test_json_and_schema_validation():
    assert json_valid().score(Subject(output='```json\n{"a": 1}\n```')).passed
    assert not json_valid().score(Subject(output="not json at all")).passed
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}
    assert schema_valid(schema).score(Subject(output='{"a": 1}')).passed
    failed = schema_valid(schema).score(Subject(output='{"b": 1}'))
    assert not failed.passed
    assert failed.details["errors"]


def test_tool_metrics_read_the_recorded_trajectory():
    agent = Agent(
        FakeModel([FakeModel.tool_call("search", {"query": "x"}), "done"]),
        tools=[search],
        name="searcher",
    )
    with start_run("outer", record=False) as run:
        agent.run("find x")
    subject = Subject.from_recorded(RecordedRun.from_run(run))
    assert tool_called("search").score(subject).passed
    assert tool_correctness(["search"]).score(subject).passed
    partial = tool_correctness(["search", "summarise"]).score(subject)
    assert not partial.passed
    assert partial.value == 0.5
    assert partial.details["missing"] == ["summarise"]


def test_tool_correctness_reads_the_expectation_from_the_dataset_item():
    metric = tool_correctness()
    assert metric.score(Subject(output="x", tool_calls=["search"], expected=["search"])).passed
    from_dict = Subject(output="x", tool_calls=["search"], expected={"tools": ["search"]})
    assert metric.score(from_dict).passed
    assert not metric.score(Subject(output="x", tool_calls=[], expected=["search"])).passed


def test_tool_correctness_can_require_an_ordered_trajectory():
    ordered = tool_correctness(["search", "summarise"], ordered=True)
    assert ordered.score(Subject(output="x", tool_calls=["search", "summarise"])).passed
    assert not ordered.score(Subject(output="x", tool_calls=["summarise", "search"])).passed


def test_tool_correctness_with_no_expected_tools_requires_none_were_called():
    metric = tool_correctness([])
    assert metric.score(Subject(output="x", tool_calls=[])).passed
    flagged = metric.score(Subject(output="x", tool_calls=["search"]))
    assert not flagged.passed
    assert "expected no tool calls" in flagged.reason


def test_exact_match_without_an_expectation_cannot_pass():
    assert not exact_match().score(Subject(output="anything")).passed


def test_json_valid_accepts_an_already_structured_response():
    score = json_valid().score(Subject(output="not json", structured={"a": 1}))
    assert score.passed
    assert score.details["source"] == "structured"


def test_schema_valid_reports_unparseable_output():
    schema = {"type": "object"}
    score = schema_valid(schema).score(Subject(output="definitely not json"))
    assert not score.passed
    assert "no JSON value" in score.reason


def test_budget_metrics():
    assert max_cost(0.01).score(Subject(output="x", cost=0.005)).passed
    assert not max_cost(0.001).score(Subject(output="x", cost=0.005)).passed
    assert max_latency(500).score(Subject(output="x", latency_ms=120)).passed
    assert not max_latency(100).score(Subject(output="x", latency_ms=120)).passed


def test_error_and_emptiness_metrics():
    assert no_errors().score(Subject(output="x")).passed
    assert not no_errors().score(Subject(output="", error="ValueError: boom")).passed
    assert not not_empty().score(Subject(output="   ")).passed


def test_evaluate_runs_every_evaluator_and_aggregates():
    result = evaluate(
        Subject(output="Paris", expected="Paris", cost=0.001),
        [exact_match(), not_empty(), max_cost(0.01)],
    )
    assert result.passed
    assert result.value == pytest.approx((1.0 + 1.0 + 0.001) / 3)
    assert set(result.by_name()) == {"exact_match", "not_empty", "max_cost"}


def test_evaluate_reports_failures():
    result = evaluate(Subject(output="Lyon", expected="Paris"), [exact_match(), not_empty()])
    assert not result.passed
    assert [s.evaluator for s in result.failures()] == ["exact_match"]


def test_subjects_can_be_coerced_from_text_and_results():
    assert coerce_subject("plain text").output == "plain text"
    agent = Agent(FakeModel(["hello"]), name="greeter")
    result = agent.run("hi")
    subject = coerce_subject(result, expected="hello")
    assert subject.output == "hello"
    assert subject.expected == "hello"
    assert subject.run_id == result.run_id


def test_coercing_an_unsupported_object_is_an_error():
    with pytest.raises(EvaluationError):
        coerce_subject(object())


async def test_llm_judge_scores_on_a_five_point_scale():
    model = FakeModel([Verdict(score=5, reason="flawless").model_dump_json()], name="judge")
    judge = LLMJudge(model, Criterion.GROUNDEDNESS)
    score = await judge.ascore(Subject(output="the sky is blue", input="what colour is the sky?"))
    assert score.value == 1.0
    assert score.passed
    assert score.label == "5/5"
    assert score.reason == "flawless"
    assert score.details["criterion"] == "groundedness"


async def test_llm_judge_fails_below_its_threshold():
    model = FakeModel([Verdict(score=2, reason="mostly wrong").model_dump_json()], name="judge")
    score = await LLMJudge(model, Criterion.CORRECTNESS).ascore(Subject(output="nonsense"))
    assert score.value == pytest.approx(0.25)
    assert not score.passed


async def test_llm_judge_turns_a_provider_failure_into_a_failing_score():
    model = FakeModel([RuntimeError("judge unavailable")], name="judge")
    score = await LLMJudge(model, Criterion.SAFETY).ascore(Subject(output="x"))
    assert not score.passed
    assert score.error is not None


def test_judge_prompt_includes_the_task_answer_and_reference():
    judge = LLMJudge(FakeModel([], name="judge"), Criterion.CORRECTNESS)
    messages = judge.build_prompt(Subject(output="Paris", input="capital?", expected="Paris"))
    body = messages[-1].text
    assert "<task>" in body
    assert "capital?" in body
    assert "<answer>" in body
    assert "<reference_answer>" in body
    assert "Paris" in body


def test_judges_satisfy_the_evaluator_interface():
    judge = LLMJudge(FakeModel([], name="judge"), Criterion.STYLE)
    assert as_evaluator(judge) is judge
    assert judge.fingerprint().startswith("sha256:")


def test_judge_panel_builds_one_judge_per_criterion():
    panel = judge_panel(FakeModel([], name="judge"))
    assert [j.criterion for j in panel] == [
        Criterion.CORRECTNESS,
        Criterion.RELEVANCE,
        Criterion.SAFETY,
    ]


async def test_aevaluate_mixes_deterministic_metrics_and_judges():
    model = FakeModel([Verdict(score=4, reason="good").model_dump_json()], name="judge")
    result = await aevaluate(
        Subject(output="Paris", expected="Paris"),
        [exact_match(), LLMJudge(model, Criterion.CORRECTNESS)],
    )
    assert result.passed
    assert len(result.scores) == 2


def test_an_evaluator_returning_nothing_scores_zero():
    @evaluator
    def silent(run):
        return None

    score = silent.score(Subject(output="x"))
    assert not score.passed
    assert score.reason == "no score"


def test_an_evaluator_returning_a_string_records_it_as_a_label():
    @evaluator
    def classify(run):
        return "on-topic"

    score = classify.score(Subject(output="x"))
    assert score.passed
    assert score.label == "on-topic"


def test_evaluator_and_judge_reprs_name_what_they_are():
    @evaluator
    def named(run):
        return True

    assert repr(named) == "Evaluator('named', threshold=0.5)"
    judge = LLMJudge(FakeModel([], name="judge"), Criterion.STYLE)
    assert repr(judge) == "LLMJudge('style', model='judge')"


def test_an_evaluator_built_from_a_builtin_still_fingerprints():
    from rewyn.evaluation.evaluator import Evaluator

    built = Evaluator(bool, name="truthy")
    assert built.fingerprint().startswith("sha256:")


def test_a_result_with_no_scores_has_no_weighted_value():
    from rewyn.evaluation.evaluator import EvaluationResult

    assert EvaluationResult().value == 0.0
    assert EvaluationResult().passed


def test_subject_accessors():
    ok = Subject(output="hello")
    assert ok.text == "hello"
    assert ok.ok
    assert not Subject(output="", error="boom").ok


def test_a_non_text_run_output_is_serialised_for_scoring():
    from rewyn.core.run import start_run
    from rewyn.replay.recorder import RecordedRun

    with start_run("structured", record=False) as run:
        run.manifest.output = {"answer": "Paris"}
    subject = Subject.from_recorded(RecordedRun.from_run(run))
    assert subject.output == '{"answer": "Paris"}'


def test_a_live_run_can_be_coerced_into_a_subject():
    from rewyn.core.run import start_run

    with start_run("live", record=False) as run:
        run.manifest.output = "Paris"
    subject = coerce_subject(run, expected="Paris")
    assert subject.output == "Paris"
    assert subject.run_id == run.id


def test_the_judge_prompt_includes_recorded_tool_results():
    from rewyn.core.run import start_run
    from rewyn.replay.recorder import RecordedRun

    agent = Agent(
        FakeModel([FakeModel.tool_call("search", {"query": "x"}), "done"]),
        tools=[search],
        name="searcher",
    )
    with start_run("outer", record=False) as run:
        agent.run("find x")
    subject = Subject.from_recorded(RecordedRun.from_run(run))
    body = LLMJudge(FakeModel([], name="judge"), Criterion.GROUNDEDNESS).build_prompt(subject)[-1]
    assert "<tool_results>" in body.text
    assert "results for x" in body.text


def test_a_judge_verdict_returned_as_a_plain_object_is_validated():
    model = FakeModel(['{"score": 3, "reason": "acceptable"}'], name="judge")
    score = LLMJudge(model, Criterion.STYLE)(Subject(output="x"))
    assert score.label == "3/5"
    assert score.value == pytest.approx(0.5)


def test_the_judge_factory_and_dependency():
    from rewyn.evaluation.judges import judge as make_judge

    built = make_judge(FakeModel([], name="judge"), Criterion.SAFETY, threshold=0.9)
    assert built.criterion is Criterion.SAFETY
    assert built.threshold == 0.9
    dependency = built.dependency
    assert dependency.kind == "evaluator"
    assert dependency.metadata["kind"] == "llm_judge"


def test_score_normalisation_rejects_unsupported_returns():
    @evaluator
    def weird(run):
        return object()

    score = weird.score(Subject(output="x"))
    assert score.error is not None


def test_a_bare_function_is_wrapped_and_called_directly():
    def says_paris(subject):
        return "Paris" in subject.output

    wrapped = as_evaluator(says_paris)
    assert wrapped.name == "says_paris"
    assert wrapped(Subject(output="Paris")).passed


def test_text_expectations_pass_through_unchanged():
    from rewyn.evaluation.evaluator import to_jsonable_text

    assert to_jsonable_text("already text") == "already text"
    assert to_jsonable_text(None) == ""
    assert to_jsonable_text({"a": 1}) == '{"a": 1}'


def test_dict_returns_become_scores():
    @evaluator
    def detailed(run):
        return {"value": 0.9, "reason": "close enough", "details": {"k": 1}}

    score = detailed.score(Subject(output="x"))
    assert isinstance(score, Score)
    assert score.passed
    assert score.details == {"k": 1}
