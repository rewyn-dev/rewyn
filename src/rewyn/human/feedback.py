"""Human feedback, corrections and escalation, recorded as run history."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import EventType
from rewyn.core.run import aensure_run
from rewyn.core.sync import run_sync
from rewyn.core.types import JSONObject, new_id

FeedbackKind = Literal["rating", "comment", "correction", "escalation"]


class Feedback(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=lambda: new_id("fb"))
    kind: FeedbackKind
    by: str = "human"
    rating: float | None = None
    comment: str | None = None
    correction: Any = None
    target: str | None = None
    escalate_to: str | None = None
    metadata: JSONObject = Field(default_factory=dict)


async def record_feedback(
    *,
    kind: FeedbackKind = "comment",
    by: str = "human",
    rating: float | None = None,
    comment: str | None = None,
    correction: Any = None,
    target: str | None = None,
    **metadata: Any,
) -> Feedback:
    feedback = Feedback(
        kind=kind,
        by=by,
        rating=rating,
        comment=comment,
        correction=correction,
        target=target,
        metadata=metadata,
    )
    async with aensure_run("human") as run:
        run.emit(EventType.HUMAN_FEEDBACK, feedback.model_dump(mode="json"))
    return feedback


async def correct(correction: Any, *, target: str | None = None, by: str = "human") -> Feedback:
    return await record_feedback(kind="correction", correction=correction, target=target, by=by)


async def escalate(
    reason: str, *, to: str, by: str = "agent", target: str | None = None
) -> Feedback:
    feedback = Feedback(kind="escalation", by=by, comment=reason, escalate_to=to, target=target)
    async with aensure_run("human") as run:
        run.emit(EventType.HUMAN_FEEDBACK, feedback.model_dump(mode="json"))
    return feedback


def record_feedback_sync(**kwargs: Any) -> Feedback:
    return run_sync(record_feedback(**kwargs))
