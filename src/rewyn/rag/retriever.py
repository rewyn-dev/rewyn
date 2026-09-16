"""Indexes and retrievers (spec §11: Embed → Index → Retrieve)."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from rewyn.core.event import EventType
from rewyn.core.run import aensure_run
from rewyn.core.span import SpanKind
from rewyn.models.pricing import compute_unit_cost
from rewyn.rag.chunker import Chunk
from rewyn.rag.embeddings import Embedder, cosine, record_embedding_cost


class Retrieved(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk: Chunk
    score: float
    rank: int

    def summary(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk.id,
            "document_id": self.chunk.document_id,
            "score": round(self.score, 6),
            "rank": self.rank,
            "hash": self.chunk.content_hash,
        }


@runtime_checkable
class Retriever(Protocol):
    name: str

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Retrieved]: ...


@runtime_checkable
class VectorIndex(Protocol):
    name: str

    async def add(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None: ...

    async def search(self, vector: Sequence[float], *, top_k: int) -> list[tuple[Chunk, float]]: ...

    async def count(self) -> int: ...


class InMemoryVectorIndex:
    name = "in_memory_vector"

    def __init__(self) -> None:
        self._entries: list[tuple[Chunk, list[float]]] = []

    async def add(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._entries.append((chunk, list(vector)))

    async def search(self, vector: Sequence[float], *, top_k: int) -> list[tuple[Chunk, float]]:
        scored = [(chunk, cosine(vector, stored)) for chunk, stored in self._entries]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    async def count(self) -> int:
        return len(self._entries)


class VectorRetriever:
    """Embed the query and search a vector index. Emits ``RETRIEVAL_QUERIED``."""

    def __init__(self, index: VectorIndex, embedder: Embedder, *, name: str = "vector") -> None:
        self.index = index
        self.embedder = embedder
        self.name = name

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Retrieved]:
        async with aensure_run("retrieval") as run:
            with run.span(f"retrieve:{self.name}", SpanKind.RETRIEVAL):
                [vector] = await self.embedder.embed([query])
                embedding_cost = record_embedding_cost(self.embedder.name, [query])
                hits = await self.index.search(vector, top_k=top_k)
                query_cost = compute_unit_cost("retrieval", self.name, calls=1, units=top_k)
                if query_cost:
                    run.record_cost("retrieval", query_cost)
                results = [
                    Retrieved(chunk=chunk, score=score, rank=rank)
                    for rank, (chunk, score) in enumerate(hits, start=1)
                ]
                run.emit(
                    EventType.RETRIEVAL_QUERIED,
                    {
                        "retriever": self.name,
                        "query": query,
                        "top_k": top_k,
                        "embedder": self.embedder.name,
                        "index": self.index.name,
                        "cost": query_cost + embedding_cost,
                        "results": [r.summary() for r in results],
                    },
                )
                return results


_WORD = re.compile(r"[a-z0-9]+")


class KeywordRetriever:
    """BM25 over an in-memory corpus; no embeddings required."""

    name = "keyword"

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[tuple[Chunk, Counter[str], int]] = []
        self._df: Counter[str] = Counter()

    def add(self, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            terms = _WORD.findall(chunk.text.lower())
            counts = Counter(terms)
            self._docs.append((chunk, counts, len(terms)))
            self._df.update(counts.keys())

    def _score(self, query_terms: list[str], counts: Counter[str], length: int) -> float:
        n = len(self._docs)
        avg = sum(d[2] for d in self._docs) / n if n else 1.0
        score = 0.0
        for term in query_terms:
            tf = counts.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
            denom = tf + self.k1 * (1 - self.b + self.b * length / avg)
            score += idf * tf * (self.k1 + 1) / denom
        return score

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Retrieved]:
        async with aensure_run("retrieval") as run:
            with run.span(f"retrieve:{self.name}", SpanKind.RETRIEVAL):
                terms = _WORD.findall(query.lower())
                scored = [
                    (chunk, self._score(terms, counts, length))
                    for chunk, counts, length in self._docs
                ]
                scored = [(c, s) for c, s in scored if s > 0]
                scored.sort(key=lambda pair: pair[1], reverse=True)
                results = [
                    Retrieved(chunk=chunk, score=score, rank=rank)
                    for rank, (chunk, score) in enumerate(scored[:top_k], start=1)
                ]
                query_cost = compute_unit_cost("retrieval", self.name, calls=1, units=top_k)
                if query_cost:
                    run.record_cost("retrieval", query_cost)
                run.emit(
                    EventType.RETRIEVAL_QUERIED,
                    {
                        "retriever": self.name,
                        "query": query,
                        "top_k": top_k,
                        "cost": query_cost,
                        "results": [r.summary() for r in results],
                    },
                )
                return results
