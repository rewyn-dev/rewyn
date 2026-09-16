"""An approval queue two processes can share (spec §26, UI spec §35).

An agent asks for approval in its own process; the person answering is
looking at a console in another one. :class:`QueueHandler` parks a request
in memory, which is enough for a UI embedded in the same process and no use
at all across two.

So this handler parks it in a directory instead. The request is written where
the console can see it, the console writes the decision back, and the agent
picks it up and carries on. The medium is the same ``.rewyn/`` the rest of
the local workflow already uses, so nothing new has to be running.

    from rewyn.human.inbox import ApprovalInbox, InboxHandler, set_default_handler

    set_default_handler(InboxHandler())      # in the agent
    inbox = ApprovalInbox()                  # in the console
    inbox.decide(request_id, approved=True, by="raj")
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from rewyn.core.types import RewynError, utcnow
from rewyn.human.approval import ApprovalDecision, ApprovalRequest
from rewyn.storage.local import atomic_write_text

POLL_INTERVAL = 0.25


class ApprovalError(RewynError):
    """An approval could not be read, written or answered."""


class PendingApproval(BaseModel):
    """One request, and the decision once someone makes it."""

    model_config = ConfigDict(extra="forbid")

    request: ApprovalRequest
    decision: ApprovalDecision | None = None
    expires_at: datetime | None = None

    @property
    def answered(self) -> bool:
        return self.decision is not None

    def expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or utcnow()) >= self.expires_at


class ApprovalInbox:
    """The shared directory: ``.rewyn/approvals/<request id>.json``."""

    def __init__(self, home: Path | None = None) -> None:
        from rewyn.core.settings import get_settings

        self.home = home if home is not None else get_settings().home

    @property
    def directory(self) -> Path:
        return self.home / "approvals"

    def path_for(self, request_id: str) -> Path:
        return self.directory / f"{request_id}.json"

    # Writing ------------------------------------------------------------------
    def submit(self, request: ApprovalRequest, *, expires_at: datetime | None = None) -> Path:
        """Publish a request for somebody to answer."""
        pending = PendingApproval(request=request, expires_at=expires_at)
        path = self.path_for(request.id)
        atomic_write_text(path, pending.model_dump_json(indent=2) + "\n")
        return path

    def decide(
        self,
        request_id: str,
        *,
        approved: bool,
        by: str = "console",
        reason: str = "",
        correction: dict[str, object] | None = None,
    ) -> PendingApproval:
        """Answer a request. The waiting agent picks this up and continues."""
        pending = self.get(request_id)
        if pending.answered:
            raise ApprovalError(f"approval {request_id!r} was already decided")
        answered = pending.model_copy(
            update={
                "decision": ApprovalDecision(
                    request_id=request_id,
                    approved=approved,
                    by=by,
                    reason=reason,
                    correction=correction,
                )
            }
        )
        atomic_write_text(self.path_for(request_id), answered.model_dump_json(indent=2) + "\n")
        return answered

    # Reading ------------------------------------------------------------------
    def get(self, request_id: str) -> PendingApproval:
        path = self.path_for(request_id)
        if not path.exists():
            raise ApprovalError(f"approval {request_id!r} was not found at {path}")
        return PendingApproval.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self, *, pending_only: bool = False) -> list[PendingApproval]:
        """Everything in the inbox, oldest first."""
        if not self.directory.exists():
            return []
        found: list[PendingApproval] = []
        for path in self.directory.glob("*.json"):
            try:
                found.append(PendingApproval.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        if pending_only:
            found = [p for p in found if not p.answered and not p.expired()]
        found.sort(key=lambda p: p.request.requested_at)
        return found

    def clear(self, *, answered_only: bool = True) -> int:
        removed = 0
        for pending in self.list():
            if answered_only and not pending.answered:
                continue
            self.path_for(pending.request.id).unlink(missing_ok=True)
            removed += 1
        return removed


class InboxHandler:
    """Ask through the inbox and wait for an answer (UI §35).

    Waiting is polling, deliberately: a directory is the one thing an agent
    and a console are guaranteed to share, and the alternative is a socket
    the developer has to run. ``timeout`` bounds the wait, and a timed-out
    request is rejected rather than left hanging, so an unattended agent
    fails closed.
    """

    name = "inbox"

    def __init__(
        self,
        inbox: ApprovalInbox | None = None,
        *,
        timeout: float | None = 300.0,
        poll_interval: float = POLL_INTERVAL,
        on_timeout: str = "reject",
    ) -> None:
        self.inbox = inbox or ApprovalInbox()
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.on_timeout = on_timeout

    async def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        expires = None
        if self.timeout is not None:
            from datetime import timedelta

            expires = utcnow() + timedelta(seconds=self.timeout)
        self.inbox.submit(request, expires_at=expires)
        waited = 0.0
        while self.timeout is None or waited < self.timeout:
            await asyncio.sleep(self.poll_interval)
            waited += self.poll_interval
            try:
                pending = self.inbox.get(request.id)
            except ApprovalError:
                break
            if pending.decision is not None:
                return pending.decision
        return ApprovalDecision(
            request_id=request.id,
            approved=self.on_timeout == "approve",
            by="system",
            reason=f"no decision within {self.timeout:.0f}s",
        )


__all__ = [
    "ApprovalError",
    "ApprovalInbox",
    "InboxHandler",
    "PendingApproval",
]
