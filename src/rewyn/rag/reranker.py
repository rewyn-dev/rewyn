"""Rerankers (spec §11: Retrieve → Rerank). Emit ``RETRIEVAL_RERANKED``."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from rewyn.core.event import EventType
from rewyn.core.run import aensure_run
from rewyn.core.span import SpanKind
from rewyn.models.base import Model, extract_json
from rewyn.rag.retriever import Retrieved

_WORD = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Reranker(Protocol):
    name: str

    async def rerank(self, query: str, results: Sequence[Retrieved]) -> list[Retrieved]: ...


async def _emit(
    name: str, query: str, before: Sequence[Retrieved], after: Sequence[Retrieved]
) -> None:
    async with aensure_run("retrieval") as run:
        run.emit(
            EventType.RETRIEVAL_RERANKED,
            {
                "reranker": name,
                "query": query,
                "before": [r.summary() for r in before],
                "after": [r.summary() for r in after],
            },
        )


class LexicalReranker:
    """Blend the retriever score with query-term overlap."""

    name = "lexical"

    def __init__(self, weight: float = 0.5) -> None:
        self.weight = weight

    async def rerank(self, query: str, results: Sequence[Retrieved]) -> list[Retrieved]:
        terms = set(_WORD.findall(query.lower()))
        rescored: list[tuple[Retrieved, float]] = []
        for hit in results:
            words = set(_WORD.findall(hit.chunk.text.lower()))
            overlap = len(terms & words) / len(terms) if terms else 0.0
            rescored.append((hit, (1 - self.weight) * hit.score + self.weight * overlap))
        rescored.sort(key=lambda pair: pair[1], reverse=True)
        after = [
            Retrieved(chunk=hit.chunk, score=round(score, 6), rank=rank)
            for rank, (hit, score) in enumerate(rescored, start=1)
        ]
        await _emit(self.name, query, results, after)
        return after


class ModelReranker:
    """Ask a model to score each chunk's relevance from 0 to 1."""

    name = "model"

    def __init__(self, model: Model) -> None:
        self.model = model

    async def rerank(self, query: str, results: Sequence[Retrieved]) -> list[Retrieved]:
        async with aensure_run("retrieval") as run:
            with run.span("rerank:model", SpanKind.RETRIEVAL):
                scores = await self._score(query, results)
        rescored = [(hit, float(scores.get(str(i), hit.score))) for i, hit in enumerate(results)]
        rescored.sort(key=lambda pair: pair[1], reverse=True)
        after = [
            Retrieved(chunk=hit.chunk, score=round(score, 6), rank=rank)
            for rank, (hit, score) in enumerate(rescored, start=1)
        ]
        await _emit(self.name, query, results, after)
        return after

    async def _score(self, query: str, results: Sequence[Retrieved]) -> dict[str, float]:
        listing = "\n\n".join(f"[{i}] {r.chunk.text}" for i, r in enumerate(results))
        response = await self.model.agenerate(
            [
                {
                    "role": "system",
                    "content": (
                        "Score how relevant each passage is to the query. Reply with a JSON "
                        'object mapping passage index to a score between 0 and 1, e.g. {"0": 0.9}.'
                    ),
                },
                {"role": "user", "content": f"Query: {query}\n\nPassages:\n{listing}"},
            ]
        )
        try:
            parsed, _ = extract_json(response.text)
        except ValueError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        scores: dict[str, float] = {}
        for key, value in parsed.items():
            if isinstance(value, int | float):
                scores[str(key)] = float(value)
        return scores
