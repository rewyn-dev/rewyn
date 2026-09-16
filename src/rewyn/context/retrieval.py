"""Bridge retrievers (RAG) into the context engine as sources."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from rewyn.context.source import ContextItem, ContextKind, Provenance, TrustLevel

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.rag.retriever import Retriever


class RetrieverSource:
    """Turn a :class:`Retriever` into a :class:`ContextSource`."""

    def __init__(
        self,
        retriever: Retriever,
        *,
        top_k: int = 5,
        name: str = "retriever",
        trust_level: TrustLevel = TrustLevel.INTERNAL,
        authority: float = 0.6,
    ) -> None:
        self.retriever = retriever
        self.top_k = top_k
        self.name = name
        self.trust_level = trust_level
        self.authority = authority

    async def fetch(self, query: str | None) -> Sequence[ContextItem]:
        if not query:
            return []
        results = await self.retriever.retrieve(query, top_k=self.top_k)
        items: list[ContextItem] = []
        for hit in results:
            chunk = hit.chunk
            items.append(
                ContextItem(
                    kind=ContextKind.KNOWLEDGE,
                    title=chunk.metadata.get("title") or chunk.document_id,
                    content=chunk.text,
                    relevance=max(0.0, min(1.0, hit.score)),
                    trust_level=self.trust_level,
                    provenance=Provenance.for_content(
                        self.name,
                        chunk.text,
                        record=chunk.id,
                        version=str(chunk.metadata.get("version", "")) or None,
                        uri=chunk.metadata.get("uri"),
                        authority=self.authority,
                    ),
                    metadata={
                        "rank": hit.rank,
                        "score": hit.score,
                        "document_id": chunk.document_id,
                    },
                )
            )
        return items
