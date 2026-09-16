"""Freshness policies: drop or flag context that is too old."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from rewyn.context.source import ContextItem
from rewyn.core.types import utcnow


class FreshnessPolicy:
    def __init__(self, max_age: timedelta, *, drop: bool = True) -> None:
        self.max_age = max_age
        self.drop = drop

    def is_fresh(self, item: ContextItem, *, now: datetime | None = None) -> bool:
        stamp = item.recency or (item.provenance.retrieved_at if item.provenance else None)
        if stamp is None:
            return True
        return (now or utcnow()) - stamp <= self.max_age

    def apply(
        self, items: Sequence[ContextItem], *, now: datetime | None = None
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        """Return ``(kept, stale)``. Stale items are dropped or flagged per ``drop``."""
        kept: list[ContextItem] = []
        stale: list[ContextItem] = []
        for item in items:
            if self.is_fresh(item, now=now):
                kept.append(item)
            elif self.drop:
                stale.append(item)
            else:
                kept.append(
                    item.model_copy(
                        update={
                            "confidence": item.confidence * 0.5,
                            "metadata": {**item.metadata, "stale": True},
                        }
                    )
                )
                stale.append(item)
        return kept, stale
