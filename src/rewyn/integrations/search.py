"""Elasticsearch and OpenSearch retrieval (spec §51).

The in-memory index is honest about what it is: fine for a demo, gone on
restart, and linear in the corpus. A real corpus lives in a search cluster,
and most teams already run one.

Two classes, because a cluster serves two different jobs. :class:`SearchRetriever`
is lexical BM25 retrieval, which is what a search engine is good at.
:class:`SearchVectorIndex` implements the vector index protocol with a dense
kNN query, so the same cluster backs the embedding path::

    pip install "rewyn[search]"

Both emit the same ``RETRIEVAL_QUERIED`` event as the built-in retrievers,
so a switch of backend does not change what a run records.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rewyn.core.event import EventType
from rewyn.core.run import aensure_run
from rewyn.core.span import SpanKind
from rewyn.core.types import MissingDependencyError
from rewyn.models.pricing import compute_unit_cost
from rewyn.rag.chunker import Chunk
from rewyn.rag.retriever import Retrieved


def _client(url: str, client: Any = None, **options: Any) -> Any:
    if client is not None:
        return client
    try:
        from elasticsearch import AsyncElasticsearch
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise MissingDependencyError("elasticsearch", "search") from exc
    return AsyncElasticsearch(url, **options)


def _chunk_from_source(source: dict[str, Any]) -> Chunk:
    return (
        Chunk.model_validate(source["chunk"])
        if "chunk" in source
        else Chunk(
            id=str(source.get("id", "")),
            document_id=str(source.get("document_id", "")),
            index=int(source.get("index", 0)),
            text=str(source.get("text", "")),
            start=int(source.get("start", 0)),
            end=int(source.get("end", 0)),
            metadata=dict(source.get("metadata") or {}),
        )
    )


class SearchRetriever:
    """Lexical retrieval over an Elasticsearch or OpenSearch index."""

    name = "search"

    def __init__(
        self,
        index: str,
        *,
        url: str = "http://localhost:9200",
        client: Any = None,
        field: str = "text",
        name: str | None = None,
        **options: Any,
    ) -> None:
        self.index = index
        self.field = field
        self.client = _client(url, client, **options)
        if name:
            self.name = name

    async def add(self, chunks: Sequence[Chunk]) -> None:
        """Index chunks. Ids are the chunk ids, so re-indexing replaces."""
        for chunk in chunks:
            await self.client.index(
                index=self.index,
                id=chunk.id,
                document={"chunk": chunk.model_dump(mode="json"), self.field: chunk.text},
            )

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Retrieved]:
        async with aensure_run("retrieval") as run:
            with run.span(f"retrieve:{self.name}", SpanKind.RETRIEVAL):
                response = await self.client.search(
                    index=self.index,
                    query={"match": {self.field: query}},
                    size=top_k,
                )
                results = [
                    Retrieved(
                        chunk=_chunk_from_source(hit["_source"]),
                        score=float(hit.get("_score") or 0.0),
                        rank=rank,
                    )
                    for rank, hit in enumerate(_hits(response), start=1)
                ]
                cost = compute_unit_cost("retrieval", self.name, calls=1, units=top_k)
                if cost:
                    run.record_cost("retrieval", cost)
                run.emit(
                    EventType.RETRIEVAL_QUERIED,
                    {
                        "retriever": self.name,
                        "query": query,
                        "top_k": top_k,
                        "index": self.index,
                        "cost": cost,
                        "results": [r.summary() for r in results],
                    },
                )
                return results


class SearchVectorIndex:
    """Dense vector search in the same cluster, via a kNN query."""

    name = "search_vector"

    def __init__(
        self,
        index: str,
        *,
        url: str = "http://localhost:9200",
        client: Any = None,
        field: str = "vector",
        **options: Any,
    ) -> None:
        self.index = index
        self.field = field
        self.client = _client(url, client, **options)

    async def add(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        for chunk, vector in zip(chunks, vectors, strict=True):
            await self.client.index(
                index=self.index,
                id=chunk.id,
                document={
                    "chunk": chunk.model_dump(mode="json"),
                    "text": chunk.text,
                    self.field: list(vector),
                },
            )

    async def search(self, vector: Sequence[float], *, top_k: int) -> list[tuple[Chunk, float]]:
        response = await self.client.search(
            index=self.index,
            knn={
                "field": self.field,
                "query_vector": list(vector),
                "k": top_k,
                "num_candidates": max(top_k * 10, 100),
            },
            size=top_k,
        )
        return [
            (_chunk_from_source(hit["_source"]), float(hit.get("_score") or 0.0))
            for hit in _hits(response)
        ]

    async def count(self) -> int:
        response = await self.client.count(index=self.index)
        return int(response.get("count", 0))


def _hits(response: Any) -> list[dict[str, Any]]:
    body = response.body if hasattr(response, "body") else response
    return list(body.get("hits", {}).get("hits", []))
