"""Compression, summarisation and deduplication of context items."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rewyn.context.budget import TokenCounter, estimate_tokens
from rewyn.context.source import ContextItem
from rewyn.models.base import Model


class Compressor(Protocol):
    async def compress(self, items: Sequence[ContextItem]) -> list[ContextItem]: ...


def dedupe(items: Sequence[ContextItem]) -> list[ContextItem]:
    """Drop items whose content is identical to an earlier item."""
    seen: set[str] = set()
    unique: list[ContextItem] = []
    for item in items:
        key = item.content_hash
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


class TruncateCompressor:
    """Cut items longer than ``max_tokens`` at a word boundary and mark them."""

    def __init__(self, max_tokens: int, *, counter: TokenCounter = estimate_tokens) -> None:
        self.max_tokens = max_tokens
        self.counter = counter

    async def compress(self, items: Sequence[ContextItem]) -> list[ContextItem]:
        out: list[ContextItem] = []
        for item in items:
            if self.counter(item.content) <= self.max_tokens:
                out.append(item)
                continue
            limit = self.max_tokens * 4
            cut = item.content[:limit].rsplit(" ", 1)[0] or item.content[:limit]
            out.append(
                item.model_copy(
                    update={
                        "content": cut + " …[truncated]",
                        "tokens": None,
                        "metadata": {**item.metadata, "compressed": "truncate"},
                    }
                )
            )
        return out


class SummaryCompressor:
    """Summarise items longer than ``max_tokens`` with a model."""

    def __init__(
        self, model: Model, max_tokens: int, *, counter: TokenCounter = estimate_tokens
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.counter = counter

    async def compress(self, items: Sequence[ContextItem]) -> list[ContextItem]:
        out: list[ContextItem] = []
        for item in items:
            if self.counter(item.content) <= self.max_tokens:
                out.append(item)
                continue
            response = await self.model.agenerate(
                [
                    {
                        "role": "system",
                        "content": (
                            f"Summarise the following {item.kind.value} content in at most "
                            f"{self.max_tokens} tokens. Preserve facts, numbers and names."
                        ),
                    },
                    {"role": "user", "content": item.content},
                ]
            )
            out.append(
                item.model_copy(
                    update={
                        "content": response.text,
                        "tokens": None,
                        "confidence": min(item.confidence, 0.8),
                        "metadata": {**item.metadata, "compressed": "summary"},
                    }
                )
            )
        return out
