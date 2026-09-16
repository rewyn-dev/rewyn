"""The demo must keep working, or it stops being a demo.

A recorded demo ages badly if the script behind it drifts from the SDK. The
whole thing runs here at zero delay, and the three moments it exists to show
are asserted rather than eyeballed.
"""

from __future__ import annotations

import pytest
from demo.__main__ import (
    QUESTION,
    act_one,
    act_three,
    act_two,
    act_two_stack,
    build_agent,
    coda,
    main,
)
from demo.scenario import ANSWER_TODAY, ANSWER_TOMORROW

from rewyn.core.event import EventType
from rewyn.replay.recorder import RecordedRun


@pytest.fixture(autouse=True)
def _no_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("demo.stage.SPEED", 0.0)


def test_the_whole_demo_runs(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    printed = capsys.readouterr().out
    assert "1. What Rewyn is" in printed
    assert "2. What it is made of" in printed
    assert "3. Why it matters" in printed
    assert "4. How you use it" in printed
    assert "Build. Run. Replay. Improve AI." in printed


def test_act_one_shows_a_run_that_recorded_itself(capsys: pytest.CaptureFixture[str]) -> None:
    run_id = act_one()
    printed = capsys.readouterr().out
    assert "$84.00" in printed
    assert "approval  tool:issue_refund by dana.ops" in printed

    recorded = RecordedRun.load(run_id)
    assert [c.name for c in recorded.tool_calls] == ["lookup_order", "issue_refund"]
    assert recorded.manifest.cost.tool > 0, "the tool cost is real, not decoration"
    assert recorded.manifest.cost.model > 0


def test_the_refund_really_needed_a_human() -> None:
    """The demo's claim about approval has to be true, not narrated."""
    from rewyn.core.run import start_run
    from rewyn.human import AutoApprove, approval_scope

    with approval_scope(AutoApprove(by="tester")), start_run("t", record=False) as run:
        build_agent(ANSWER_TODAY).run(QUESTION)

    assert run.events_of(EventType.HUMAN_APPROVAL_REQUESTED)
    approved = run.events_of(EventType.HUMAN_APPROVED)[0]
    assert approved.payload["action"] == "tool:issue_refund"

    returned = [
        e for e in run.events_of(EventType.TOOL_RETURNED) if e.payload["name"] == "issue_refund"
    ]
    assert returned[0].payload["is_error"] is False, "approval must let the call through"


async def test_the_full_stack_act_uses_every_subsystem(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Act two claims a list of primitives. The run has to actually use them."""
    run_id = await act_two_stack()
    printed = capsys.readouterr().out

    assert "triage → research → resolve → sign_off" in printed
    assert "token deltas, live" in printed

    recorded = RecordedRun.load(run_id)
    kinds = {d.kind for d in recorded.manifest.dependencies}
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
    } <= kinds, f"a claimed subsystem was not used: {kinds}"


async def test_the_hostile_email_is_shown_but_outranked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The demo's headline claim: untrusted input cannot set policy."""
    run_id = await act_two_stack()
    capsys.readouterr()
    recorded = RecordedRun.load(run_id)

    assembled = recorded.events_of(EventType.CONTEXT_ASSEMBLED)[-1].payload
    levels = {r["trust_level"] for r in assembled["provenance"]}
    assert "untrusted" in levels

    prompts = ["".join(m.text for m in c.messages) for c in recorded.model_calls]
    boxed = [p for p in prompts if "<untrusted" in p]
    assert boxed, "the email must be shown to the model, boxed"
    assert "process $900" in boxed[0], "shown, not dropped"
    assert "## Skills" in boxed[0], "the policy is in the same prompt and outranks it"

    assert "$900" not in str(recorded.output) or "declined" in str(recorded.output)


async def test_the_full_stack_run_costs_more_than_its_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Four of five cost categories are real here, not decoration."""
    run_id = await act_two_stack()
    capsys.readouterr()
    cost = RecordedRun.load(run_id).manifest.cost
    assert cost.model > 0
    assert cost.tool > 0
    assert cost.retrieval > 0
    assert cost.embedding > 0
    assert cost.total > cost.model


def test_act_two_finds_silent_drift(capsys: pytest.CaptureFixture[str]) -> None:
    first = act_one()
    capsys.readouterr()
    act_two(first)
    printed = capsys.readouterr().out

    assert "behaviour changed with no dependency change" in printed
    assert "identical  True" in printed
    assert "0 provider calls" in printed
    assert "hypotheses, never facts" in printed


def test_act_three_ships_one_version_and_stops_the_other(
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = act_one()
    capsys.readouterr()
    act_three(first)
    printed = capsys.readouterr().out

    assert printed.count("Result:") == 2, "one gate passes, one fails"
    assert "PASS" in printed
    assert "FAIL" in printed
    assert "offered credit the policy does not allow" in printed
    assert "It does not ship." in printed


def test_the_two_answers_differ_only_in_the_way_the_demo_claims() -> None:
    assert "goodwill" in ANSWER_TOMORROW.lower()
    assert "goodwill" not in ANSWER_TODAY.lower()
    assert "$84.00" in ANSWER_TODAY
    assert "$84.00" in ANSWER_TOMORROW


def test_the_closing_manifest_lists_real_dependencies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = act_one()
    capsys.readouterr()
    coda(run_id)
    printed = capsys.readouterr().out
    assert "agents:" in printed
    assert "support v3" in printed
