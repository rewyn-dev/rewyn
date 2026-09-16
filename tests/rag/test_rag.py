from __future__ import annotations

import pytest

from rewyn.context import Context
from rewyn.core.event import EventType, ListSink
from rewyn.core.run import start_run
from rewyn.rag import (
    Document,
    FixedSizeChunker,
    HashingEmbedder,
    KeywordRetriever,
    LexicalReranker,
    ModelReranker,
    ParagraphChunker,
    RAGPipeline,
    cosine,
)
from rewyn.testing import FakeModel

DOCS = [
    Document(
        text="Acme Corporation credit history: paid every invoice within 30 days for five years.",
        title="Acme credit",
        source="finance",
        version="3",
    ),
    Document(
        text="Globex Corporation missed three payments in 2025 and disputed two invoices.",
        title="Globex credit",
        source="finance",
    ),
    Document(text="The office kitchen will be renovated next month.", title="Facilities"),
]


def test_fixed_size_chunker_overlaps_and_snaps_to_spaces() -> None:
    doc = Document(text=" ".join(f"w{i}" for i in range(200)), title="t", version="1")
    chunks = FixedSizeChunker(chunk_size=100, overlap=20).chunk(doc)
    assert len(chunks) > 5
    assert all(len(c.text) <= 100 for c in chunks)
    assert all(not c.text.startswith(" ") for c in chunks)
    assert chunks[1].start < chunks[0].end  # overlap
    assert chunks[0].metadata == {"source": "inline", "title": "t", "version": "1"}
    assert [c.index for c in chunks] == list(range(len(chunks)))
    with pytest.raises(ValueError, match="overlap"):
        FixedSizeChunker(chunk_size=10, overlap=10)


def test_paragraph_chunker_merges_short_paragraphs() -> None:
    doc = Document(text="one\n\ntwo\n\n" + "x" * 50 + "\n\nlast")
    chunks = ParagraphChunker(max_chars=20).chunk(doc)
    assert [c.text for c in chunks] == ["one\n\ntwo", "x" * 50, "last"]
    assert chunks[0].start == 0
    assert chunks[2].text == doc.text[chunks[2].start : chunks[2].end].strip()


async def test_hashing_embedder_is_deterministic_and_semantic_ish() -> None:
    embedder = HashingEmbedder(dimension=64)
    [a, b, c] = await embedder.embed(
        ["credit invoice payment", "invoice payment credit", "kitchen"]
    )
    assert a == (await embedder.embed(["credit invoice payment"]))[0]
    assert cosine(a, b) > cosine(a, c)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


async def test_pipeline_indexes_retrieves_and_records_events() -> None:
    sink = ListSink()
    pipeline = RAGPipeline(top_k=2, reranker=LexicalReranker())
    async with start_run("t", sinks=[sink]) as run:
        chunks = await pipeline.index_documents(DOCS)
        result = await pipeline.retrieve("Acme invoice payment history")
    assert len(chunks) == 3
    assert result.final[0].chunk.document_id == DOCS[0].id
    assert result.reranked is not None
    assert result.context[0].provenance is not None
    assert result.context[0].provenance.source == "rag"
    assert result.context[0].provenance.version == "3"
    assert result.texts[0].startswith("Acme")
    indexed = sink.of_type(EventType.DOCUMENT_INDEXED)
    assert [e.payload["title"] for e in indexed] == ["Acme credit", "Globex credit", "Facilities"]
    assert indexed[0].payload["chunks"][0]["id"] == f"{DOCS[0].id}:0"
    queried = sink.of_type(EventType.RETRIEVAL_QUERIED)[0]
    assert queried.payload["embedder"] == "hashing"
    assert len(queried.payload["results"]) == 2
    reranked = sink.of_type(EventType.RETRIEVAL_RERANKED)[0]
    assert reranked.payload["reranker"] == "lexical"
    assert [r["rank"] for r in reranked.payload["after"]] == [1, 2]
    assert run.manifest.dependencies[0].kind == "retriever"
    assert pipeline.fingerprint() != RAGPipeline().fingerprint()


def test_pipeline_sync_and_context_source() -> None:
    pipeline = RAGPipeline(top_k=1)
    pipeline.index_documents_sync(DOCS)
    assert pipeline.retrieve_sync("Globex missed payments").final[0].chunk.document_id == DOCS[1].id
    context = Context([pipeline.as_context_source()], budget=400)
    assembled = context.assemble_sync("Globex disputed invoices")
    assert "Globex" in assembled.text()
    assert assembled.items[0].metadata["rank"] == 1


async def test_keyword_retriever_bm25() -> None:
    retriever = KeywordRetriever()
    retriever.add(FixedSizeChunker().chunk(DOCS[0]) + FixedSizeChunker().chunk(DOCS[1]))
    hits = await retriever.retrieve("missed payments", top_k=5)
    assert hits[0].chunk.document_id == DOCS[1].id
    assert await retriever.retrieve("nothing matches", top_k=5) == []


async def test_model_reranker_uses_model_scores() -> None:
    pipeline = RAGPipeline(top_k=3, reranker=ModelReranker(FakeModel(['{"2": 0.99, "0": 0.1}'])))
    await pipeline.index_documents(DOCS)
    result = await pipeline.retrieve("Acme")
    assert result.reranked is not None
    assert result.reranked[0].score == 0.99
    assert result.reranked[0].chunk.document_id == result.retrieved[2].chunk.document_id
