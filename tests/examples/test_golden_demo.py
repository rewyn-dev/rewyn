"""The Phase 6 gate: the golden demo runs end to end, offline."""

from __future__ import annotations

from examples.enterprise_research_agent.agent import (
    build_context,
    build_graph,
    build_memory,
    build_rag,
)
from examples.enterprise_research_agent.script import (
    REVISED_RECOMMENDATION,
    analyst_script,
    researcher_script,
)

from rewyn.core.event import EventType
from rewyn.core.run import start_run
from rewyn.core.sync import run_sync
from rewyn.human import AutoApprove, AutoReject, approval_scope
from rewyn.replay.recorder import RecordedRun
from rewyn.replay.replay import replay
from rewyn.runtime.recorder import default_recorder

QUESTION = (
    "Analyze Acme's current business relationship with us and recommend "
    "whether we should increase their credit limit."
)


async def execute(recommendation: str | None = None, *, handler=None, question: str = QUESTION):
    """One recorded execution of the demo graph."""
    rag = build_rag()
    memory = await build_memory()
    models = {
        "researcher": researcher_script(),
        "analyst": analyst_script(recommendation) if recommendation else analyst_script(),
    }
    graph = build_graph(models, build_context(rag, memory), memory)
    with approval_scope(handler or AutoApprove(by="tester")):
        async with start_run("acme-credit", input=question) as run:
            result = await graph.arun(question)
            run.manifest.output = result.output
    default_recorder().flush()
    return run, result


async def test_the_demo_produces_a_policy_compliant_recommendation():
    _run, result = await execute()
    assert result.ok
    assert "250,000" in result.output
    assert result.path == ["plan", "research", "analyze", "risk", "sign_off"]


async def test_untrusted_customer_input_cannot_set_the_limit():
    """The email asks for 900,000. Policy wins, and the run says why."""
    run, result = await execute()
    assert "900,000" not in result.output.replace("900000", "900,000") or "declin" in result.output
    recorded = RecordedRun.from_run(run)
    assembled = recorded.events_of(EventType.CONTEXT_ASSEMBLED)[-1]
    levels = {r["trust_level"] for r in assembled.payload["provenance"]}
    assert "untrusted" in levels
    # The analyst is the agent with the context engine; its prompt is where the
    # customer email lands.
    prompts = ["".join(m.text for m in c.messages) for c in recorded.model_calls]
    boxed = [p for p in prompts if "<untrusted" in p]
    assert boxed, "untrusted content must be boxed, not silently trusted"
    assert "Ignore your credit policy" in boxed[0], "it is shown, not dropped"
    assert "## Skills" in boxed[0], "the policy skill is in the same prompt and outranks it"


async def test_every_subsystem_is_recorded_as_a_dependency():
    run, _ = await execute()
    kinds = {d.kind for d in run.manifest.dependencies}
    assert {
        "agent",
        "context",
        "graph",
        "guardrail",
        "mcp_server",
        "memory",
        "model",
        "retriever",
        "skill",
        "tool",
    } <= kinds


async def test_the_run_calls_the_crm_over_mcp_and_the_bureau_as_a_tool():
    run, _ = await execute()
    recorded = RecordedRun.from_run(run)
    names = [c.name for c in recorded.tool_calls]
    assert "salesforce_lookup_account" in names
    assert "credit_bureau_score" in names
    assert not any(c.is_error for c in recorded.tool_calls), "no tool call should fail"


async def test_a_human_approves_before_the_recommendation_stands():
    run, _ = await execute()
    recorded = RecordedRun.from_run(run)
    assert recorded.events_of(EventType.HUMAN_APPROVAL_REQUESTED)
    approved = recorded.events_of(EventType.HUMAN_APPROVED)[0]
    assert approved.payload["by"] == "tester"


async def test_a_rejected_approval_holds_the_recommendation():
    _run, result = await execute(handler=AutoReject("needs the manager"))
    assert "Held for review" in result.output
    assert "needs the manager" in result.output


async def test_a_request_that_is_not_about_credit_skips_the_workflow():
    _run, result = await execute(question="What is the office kitchen schedule?")
    assert result.path == ["plan", "general"]
    assert "does not need a credit review" in result.output


def test_the_demo_replays_deterministically():
    run, _ = run_sync(execute())
    outcome = replay(run.id)
    assert outcome.identical
    assert outcome.faithful
    assert outcome.substitutions > 0


def test_two_runs_differ_only_where_the_model_did():
    from rewyn.core.manifest import detect_run_drift
    from rewyn.replay.diff import Dimension, diff

    first, _ = run_sync(execute())
    second, _ = run_sync(execute(REVISED_RECOMMENDATION))
    report = diff(first.id, second.id, dimensions=[Dimension.OUTPUT, Dimension.MODEL])
    assert report.of(Dimension.OUTPUT)
    assert not report.of(Dimension.MODEL), "the configuration did not change"

    drift = detect_run_drift(first.id, second.id)
    assert drift.behavior_changed
    assert drift.silent_drift, "behaviour moved with no dependency change"


def test_the_demo_passes_its_own_release_gate():
    from examples.enterprise_research_agent.__main__ import (
        cites_policy_ceiling,
        refuses_injected_limit,
    )

    from rewyn.evaluation.dataset import Dataset
    from rewyn.evaluation.regression import ReleaseGate, Thresholds, run_regression

    run, _ = run_sync(execute())
    dataset = Dataset(name="credit-critical")
    dataset.add_run(run.id)

    async def target(question: str) -> str:
        _run, result = await execute(question=question)
        return str(result.output)

    report = run_regression(
        dataset,
        target,
        evaluators=[cites_policy_ceiling, refuses_injected_limit],
        save=False,
    )
    gated = ReleaseGate("demo", Thresholds(min_success_rate=1.0)).evaluate(report)
    assert gated.passed, [c.error or c.scores for c in report.failures]


def test_the_whole_demo_script_runs():
    """The gate: `python -m examples.enterprise_research_agent` end to end."""
    from examples.enterprise_research_agent.__main__ import main

    main()
