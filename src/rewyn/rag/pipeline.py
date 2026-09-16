"""The observable RAG pipeline (spec §11).

Document → Chunk → Embed → Index → Retrieve → Rerank → Context. Every stage
records what happened so retrieval behaviour is replayable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.context.retrieval import RetrieverSource
from rewyn.context.source import ContextItem, TrustLevel
from rewyn.core.event import EventType
from rewyn.core.run import DependencyRef, aensure_run
from rewyn.core.schema import fingerprint
from rewyn.core.span import SpanKind
from rewyn.core.sync import run_sync
from rewyn.rag.chunker import Chunk, Chunker, Document, FixedSizeChunker
from rewyn.rag.embeddings import Embedder, HashingEmbedder, record_embedding_cost
from rewyn.rag.reranker import Reranker
from rewyn.rag.retriever import InMemoryVectorIndex, Retrieved, VectorIndex, VectorRetriever


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    retrieved: list[Retrieved]
    reranked: list[Retrieved] | None = None
    context: list[ContextItem] = Field(default_factory=list)

    @property
    def final(self) -> list[Retrieved]:
        return self.reranked if self.reranked is not None else self.retrieved

    @property
    def texts(self) -> list[str]:
        return [r.chunk.text for r in self.final]


class RAGPipeline:
    def __init__(
        self,
        *,
        chunker: Chunker | None = None,
        embedder: Embedder | None = None,
        index: VectorIndex | None = None,
        reranker: Reranker | None = None,
        top_k: int = 5,
        name: str = "rag",
        version: str = "1",
        trust_level: TrustLevel = TrustLevel.INTERNAL,
    ) -> None:
        self.chunker = chunker or FixedSizeChunker()
        self.embedder = embedder or HashingEmbedder()
        self.index = index or InMemoryVectorIndex()
        self.reranker = reranker
        self.top_k = top_k
        self.name = name
        self.version = version
        self.trust_level = trust_level
        self.retriever = VectorRetriever(self.index, self.embedder, name=f"{name}:vector")
        self.documents: dict[str, Document] = {}
        self.chunks: dict[str, Chunk] = {}

    # Identity ------------------------------------------------------------------
    def config(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "chunker": self.chunker.name,
            "embedder": self.embedder.name,
            "index": self.index.name,
            "reranker": self.reranker.name if self.reranker else None,
            "top_k": self.top_k,
            "documents": sorted(d.content_hash for d in self.documents.values()),
        }

    def fingerprint(self) -> str:
        return fingerprint(self.config())

    @property
    def dependency(self) -> DependencyRef:
        return DependencyRef(
            kind="retriever", name=self.name, version=self.version, fingerprint=self.fingerprint()
        )

    # Indexing ------------------------------------------------------------------
    async def index_documents(self, documents: Iterable[Document | str]) -> list[Chunk]:
        docs = [d if isinstance(d, Document) else Document(text=d) for d in documents]
        all_chunks: list[Chunk] = []
        async with aensure_run("rag") as run:
            run.add_dependency(self.dependency)
            with run.span(f"rag:index:{self.name}", SpanKind.RETRIEVAL):
                for document in docs:
                    chunks = self.chunker.chunk(document)
                    texts = [c.text for c in chunks]
                    vectors = await self.embedder.embed(texts)
                    embedding_cost = record_embedding_cost(self.embedder.name, texts)
                    await self.index.add(chunks, vectors)
                    self.documents[document.id] = document
                    for chunk in chunks:
                        self.chunks[chunk.id] = chunk
                    all_chunks.extend(chunks)
                    run.emit(
                        EventType.DOCUMENT_INDEXED,
                        {
                            "pipeline": self.name,
                            "document_id": document.id,
                            "title": document.title,
                            "source": document.source,
                            "version": document.version,
                            "hash": document.content_hash,
                            "chunker": self.chunker.name,
                            "embedder": self.embedder.name,
                            "cost": embedding_cost,
                            "chunks": [
                                {"id": c.id, "hash": c.content_hash, "chars": len(c.text)}
                                for c in chunks
                            ],
                        },
                    )
        return all_chunks

    def index_documents_sync(self, documents: Iterable[Document | str]) -> list[Chunk]:
        return run_sync(self.index_documents(documents))

    # Retrieval -----------------------------------------------------------------
    async def retrieve(self, query: str, *, top_k: int | None = None) -> RetrievalResult:
        k = top_k or self.top_k
        async with aensure_run("rag") as run:
            run.add_dependency(self.dependency)
            with run.span(f"rag:retrieve:{self.name}", SpanKind.RETRIEVAL):
                retrieved = await self.retriever.retrieve(query, top_k=k)
                reranked = None
                if self.reranker is not None and retrieved:
                    reranked = await self.reranker.rerank(query, retrieved)
                result = RetrievalResult(query=query, retrieved=retrieved, reranked=reranked)
                result.context = await self._to_context(result.final)
                return result

    def retrieve_sync(self, query: str, **kwargs: Any) -> RetrievalResult:
        return run_sync(self.retrieve(query, **kwargs))

    async def _to_context(self, hits: Sequence[Retrieved]) -> list[ContextItem]:
        source = RetrieverSource(_StaticHits(hits), name=self.name, trust_level=self.trust_level)
        return list(await source.fetch("_"))

    def as_context_source(self, *, top_k: int | None = None) -> RetrieverSource:
        return RetrieverSource(
            _PipelineRetriever(self),
            top_k=top_k or self.top_k,
            name=self.name,
            trust_level=self.trust_level,
        )


class _StaticHits:
    name = "static"

    def __init__(self, hits: Sequence[Retrieved]) -> None:
        self.hits = list(hits)

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Retrieved]:
        return self.hits[:top_k]


class _PipelineRetriever:
    def __init__(self, pipeline: RAGPipeline) -> None:
        self.pipeline = pipeline
        self.name = pipeline.name

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[Retrieved]:
        return (await self.pipeline.retrieve(query, top_k=top_k)).final
