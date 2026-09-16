from __future__ import annotations

import asyncio

import pytest

from rewyn import Agent, tool
from rewyn.core.event import EventType, ListSink
from rewyn.core.run import start_run
from rewyn.human import (
    ApprovalDecision,
    ApprovalRequest,
    AutoApprove,
    AutoReject,
    CallbackHandler,
    ConsoleHandler,
    QueueHandler,
    approval_scope,
    approve,
    approve_sync,
    correct,
    escalate,
    record_feedback,
    set_default_handler,
)
from rewyn.testing import FakeModel
from rewyn.tools import MaxRiskLevel, RiskLevel


async def test_approve_records_request_and_decision() -> None:
    sink = ListSink()
    async with start_run("t", sinks=[sink]):
        with approval_scope(AutoApprove(by="ann")):
            decision = await approve(action="issue_refund", amount=85000)
        rejected = await approve(action="delete_everything")  # default handler rejects
    assert decision
    assert decision.by == "ann"
    assert not rejected
    assert "no approval handler" in rejected.reason
    requested = sink.of_type(EventType.HUMAN_APPROVAL_REQUESTED)
    assert requested[0].payload["action"] == "issue_refund"
    assert requested[0].payload["details"] == {"amount": 85000}
    assert requested[0].payload["handler"] == "auto_approve"
    assert sink.of_type(EventType.HUMAN_APPROVED)[0].payload["by"] == "ann"
    assert sink.of_type(EventType.HUMAN_REJECTED)[0].payload["action"] == "delete_everything"


def test_sync_and_callback_handlers() -> None:
    seen: list[ApprovalRequest] = []

    def decide(request: ApprovalRequest) -> bool:
        seen.append(request)
        return request.details.get("amount", 0) < 100

    with approval_scope(CallbackHandler(decide, by="rule")):
        assert approve_sync("pay", amount=50).approved
        assert not approve_sync("pay", amount=500).approved
    assert [r.action for r in seen] == ["pay", "pay"]

    async def async_decide(request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(request_id=request.id, approved=True, by="async", reason="ok")

    with approval_scope(CallbackHandler(async_decide)):
        assert approve_sync("go").by == "async"


async def test_console_and_queue_handlers() -> None:
    console = ConsoleHandler(prompt=lambda text: "y")
    assert (await approve("x", handler=console)).approved
    console_no = ConsoleHandler(prompt=lambda text: "")
    assert not (await approve("x", handler=console_no)).approved

    queue = QueueHandler(timeout=2)

    async def request() -> ApprovalDecision:
        return await approve("transfer", handler=queue, amount=1)

    task = asyncio.ensure_future(request())
    await asyncio.sleep(0.01)
    [request_id] = list(queue.pending)
    queue.resolve(request_id, True, by="ops", reason="looks fine", correction={"amount": 2})
    decision = await task
    assert decision.approved
    assert decision.correction == {"amount": 2}
    assert queue.pending == {}
    with pytest.raises(KeyError):
        queue.resolve("nope", True)
    timed = QueueHandler(timeout=0.01)
    assert "timed out" in (await approve("slow", handler=timed)).reason


async def test_tool_approval_through_agent() -> None:
    @tool(risk_level="critical")
    def wire(amount: int) -> str:
        """Wire money."""
        return f"wired {amount}"

    sink = ListSink()
    model = FakeModel([FakeModel.tool_call("wire", {"amount": 5}), "done"])
    agent = Agent(
        model=model,
        tools=[wire],
        permission_policy=MaxRiskLevel(approve_above=RiskLevel.MEDIUM),
        approval_handler=AutoReject("not today"),
    )
    async with start_run("t", sinks=[sink]):
        result = await agent.arun("wire 5")
    assert result.messages[2].tool_results[0].is_error
    assert "approval was not granted" in result.messages[2].tool_results[0].content
    assert sink.of_type(EventType.HUMAN_REJECTED)[0].payload["action"] == "tool:wire"
    assert sink.of_type(EventType.TOOL_DENIED)[0].payload["name"] == "wire"

    approving = Agent(
        model=FakeModel([FakeModel.tool_call("wire", {"amount": 5}), "done"]),
        tools=[wire],
        permission_policy=MaxRiskLevel(approve_above=RiskLevel.MEDIUM),
        approval_handler=AutoApprove(),
    )
    ok = await approving.arun("wire 5")
    assert ok.messages[2].tool_results[0].content == "wired 5"


async def test_feedback_and_escalation_events() -> None:
    sink = ListSink()
    async with start_run("t", sinks=[sink]):
        fb = await record_feedback(kind="rating", rating=0.8, comment="good", by="reviewer")
        await correct({"amount": 10}, target="evt_1")
        await escalate("needs a human", to="finance-lead")
    events = sink.of_type(EventType.HUMAN_FEEDBACK)
    assert [e.payload["kind"] for e in events] == ["rating", "correction", "escalation"]
    assert events[0].payload["id"] == fb.id
    assert events[1].payload["correction"] == {"amount": 10}
    assert events[2].payload["escalate_to"] == "finance-lead"


def test_default_handler_override() -> None:
    set_default_handler(AutoApprove(by="default"))
    try:
        assert approve_sync("anything").by == "default"
    finally:
        set_default_handler(AutoReject())
    assert not approve_sync("anything").approved
