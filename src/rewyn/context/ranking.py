"""Priority scoring for context items (spec §8 priority signals)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from rewyn.context.source import ContextItem, TrustLevel
from rewyn.core.types import utcnow


class PriorityWeights(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relevance: float = 0.35
    recency: float = 0.10
    authority: float = 0.15
    user_importance: float = 0.15
    task_importance: float = 0.15
    provenance: float = 0.05
    confidence: float = 0.05
    recency_half_life: timedelta = timedelta(days=7)


class Ranker(Protocol):
    def score(self, item: ContextItem, *, now: datetime | None = None) -> float: ...


class DefaultRanker:
    """Weighted sum of the spec's priority signals, in [0, 1]."""

    def __init__(self, weights: PriorityWeights | None = None) -> None:
        self.weights = weights or PriorityWeights()

    def score(self, item: ContextItem, *, now: datetime | None = None) -> float:
        w = self.weights
        now = now or utcnow()
        recency = 0.5
        if item.recency is not None:
            age = max(0.0, (now - item.recency).total_seconds())
            half_life = max(1.0, w.recency_half_life.total_seconds())
            recency = math.pow(0.5, age / half_life)
        provenance = 0.0 if item.provenance is None else 1.0
        trust_bonus = item.trust_level.rank / TrustLevel.SYSTEM.rank
        score = (
            w.relevance * item.relevance
            + w.recency * recency
            + w.authority * item.effective_authority
            + w.user_importance * item.user_importance
            + w.task_importance * item.task_importance
            + w.provenance * provenance * trust_bonus
            + w.confidence * item.confidence
        )
        return max(0.0, min(1.0, score))
