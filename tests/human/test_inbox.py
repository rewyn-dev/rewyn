"""Approving from somewhere else (spec §26, UI spec §35).

The agent and the person answering are in different processes, so these
tests drive both sides: one asks, the other answers, and the answer has to
reach the agent and become part of its run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rewyn.core.event import EventType
from rewyn.core.run import start_run
from rewyn.human.approval import ApprovalRequest, approval_scope, approve
from rewyn.human.inbox import ApprovalError, ApprovalInbox, InboxHandler


def test_a_request_is_visible_to_anything_reading_the_directory(rewyn_home: Path):
    inbox = ApprovalInbox(rewyn_home)
    request = ApprovalRequest(action="refund", details={"amount": 85_000}, risk="high")
    path = inbox.submit(request)

    assert path.exists()
    # A second inbox object is a stand-in for the console process.
    pending = ApprovalInbox(rewyn_home).list(pending_only=True)
    assert [p.request.id for p in pending] == [request.id]
    assert pending[0].request.details == {"amount": 85_000}
    assert pending[0].answered is False


def test_deciding_twice_is_refused(rewyn_home: Path):
    inbox = ApprovalInbox(rewyn_home)
    request = ApprovalRequest(action="refund")
    inbox.submit(request)

    answered = inbox.decide(request.id, approved=True, by="raj", reason="within policy")
    assert answered.decision is not None
    assert answered.decision.approved is True
    assert answered.decision.by == "raj"

    with pytest.raises(ApprovalError, match="already decided"):
        inbox.decide(request.id, approved=False)


def test_an_unknown_request_says_where_it_looked(rewyn_home: Path):
    with pytest.raises(ApprovalError, match="approvals"):
        ApprovalInbox(rewyn_home).get("apr_nope")


async def test_the_agent_waits_and_the_decision_becomes_part_of_the_run(rewyn_home: Path):
    """UI §35: "The decision becomes part of the run"."""
    inbox = ApprovalInbox(rewyn_home)
    handler = InboxHandler(inbox, timeout=5.0, poll_interval=0.02)

    async def answer() -> None:
        for _ in range(200):
            pending = inbox.list(pending_only=True)
            if pending:
                inbox.decide(pending[0].request.id, approved=True, by="raj", reason="ok")
                return
            await asyncio.sleep(0.02)

    with approval_scope(handler):
        async with start_run("refund") as run:
            decision, _ = await asyncio.gather(
                approve("issue_refund", amount=85_000, risk="high"), answer()
            )

    assert decision.approved is True
    assert decision.by == "raj"
    types = [event.type for event in run.events]
    assert EventType.HUMAN_APPROVAL_REQUESTED in types
    assert EventType.HUMAN_APPROVED in types


async def test_an_unanswered_request_fails_closed(rewyn_home: Path):
    """Nobody is watching: the agent must not proceed on its own say-so."""
    handler = InboxHandler(ApprovalInbox(rewyn_home), timeout=0.1, poll_interval=0.02)
    with approval_scope(handler):
        decision = await approve("issue_refund", amount=85_000)
    assert decision.approved is False
    assert "no decision" in decision.reason


async def test_the_timeout_can_be_configured_to_approve(rewyn_home: Path):
    handler = InboxHandler(
        ApprovalInbox(rewyn_home), timeout=0.1, poll_interval=0.02, on_timeout="approve"
    )
    with approval_scope(handler):
        decision = await approve("read_report")
    assert decision.approved is True


def test_answered_requests_can_be_cleared(rewyn_home: Path):
    inbox = ApprovalInbox(rewyn_home)
    first = ApprovalRequest(action="a")
    second = ApprovalRequest(action="b")
    inbox.submit(first)
    inbox.submit(second)
    inbox.decide(first.id, approved=True)

    assert inbox.clear() == 1
    assert [p.request.action for p in inbox.list()] == ["b"]
