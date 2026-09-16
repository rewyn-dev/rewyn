"""Token budgeting (spec §8): decide what deserves the available budget.

The allocator never silently discards context. It returns a
:class:`BudgetDecision` naming every included and excluded item with the
reason, and raises when *required* items alone do not fit.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from rewyn.context.source import ContextItem
from rewyn.core.types import BudgetExceededError, JSONObject


class TokenCounter(Protocol):
    def __call__(self, text: str) -> int: ...


def estimate_tokens(text: str) -> int:
    """Provider-independent estimate (~4 characters per token)."""
    return max(1, math.ceil(len(text) / 4))


class IncludedItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: str
    title: str | None
    tokens: int
    score: float


class ExcludedItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: str
    title: str | None
    tokens: int
    score: float
    reason: str


class BudgetDecision(BaseModel):
    """The exposed outcome of budget allocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    budget: int
    used: int
    included: list[IncludedItem] = Field(default_factory=list)
    excluded: list[ExcludedItem] = Field(default_factory=list)

    @property
    def remaining(self) -> int:
        return self.budget - self.used

    def by_kind(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for item in self.included:
            totals[item.kind] = totals.get(item.kind, 0) + item.tokens
        return totals

    def render(self) -> str:
        lines = [f"Context budget: {self.budget:,} tokens (used {self.used:,})", "", "Included:"]
        for kind, tokens in self.by_kind().items():
            lines.append(f"  {kind:<12} {tokens:>8,}")
        if self.excluded:
            lines.append("")
            lines.append("Excluded:")
            for item in self.excluded:
                label = item.title or item.id
                lines.append(f"  {label} ({item.kind}, {item.tokens:,} tokens): {item.reason}")
        return "\n".join(lines)

    def to_payload(self) -> JSONObject:
        return self.model_dump(mode="json") | {"by_kind": self.by_kind()}


Scorer = Callable[[ContextItem], float]


def allocate(
    items: Sequence[ContextItem],
    budget: int,
    scorer: Scorer,
    *,
    counter: TokenCounter = estimate_tokens,
) -> tuple[list[ContextItem], BudgetDecision]:
    """Greedy allocation: required items first, then by descending score.

    Items are returned in their original order so rendering stays stable.
    """
    sized: list[tuple[int, ContextItem, int, float]] = []
    for index, item in enumerate(items):
        tokens = item.tokens if item.tokens is not None else counter(item.content)
        item.tokens = tokens
        sized.append((index, item, tokens, scorer(item)))

    required = [entry for entry in sized if entry[1].required]
    optional = [entry for entry in sized if not entry[1].required]
    required_tokens = sum(entry[2] for entry in required)
    if required_tokens > budget:
        names = ", ".join(entry[1].title or entry[1].id for entry in required)
        raise BudgetExceededError(
            f"required context ({required_tokens} tokens: {names}) exceeds budget {budget}"
        )

    used = required_tokens
    included_indices = {entry[0] for entry in required}
    included = [
        IncludedItem(
            id=e[1].id, kind=e[1].kind.value, title=e[1].title, tokens=e[2], score=round(e[3], 4)
        )
        for e in required
    ]
    excluded: list[ExcludedItem] = []
    for index, item, tokens, score in sorted(optional, key=lambda e: (-e[3], e[0])):
        if used + tokens <= budget:
            used += tokens
            included_indices.add(index)
            included.append(
                IncludedItem(
                    id=item.id,
                    kind=item.kind.value,
                    title=item.title,
                    tokens=tokens,
                    score=round(score, 4),
                )
            )
        else:
            excluded.append(
                ExcludedItem(
                    id=item.id,
                    kind=item.kind.value,
                    title=item.title,
                    tokens=tokens,
                    score=round(score, 4),
                    reason=f"budget: needs {tokens} tokens, {budget - used} remaining",
                )
            )
    chosen = [entry[1] for entry in sized if entry[0] in included_indices]
    decision = BudgetDecision(budget=budget, used=used, included=included, excluded=excluded)
    return chosen, decision
