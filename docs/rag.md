# RAG

## Concept

Retrieval-augmented generation is usually a black box bolted onto a prompt:
text goes in, chunks come out, and when the answer is wrong nobody can say
which stage was at fault. Was it chunking, embedding, the index, the
reranker, or the budget that dropped the chunk before the model saw it?

Rewyn makes each stage observable. Indexing emits `DOCUMENT_INDEXED`,
retrieval emits `RETRIEVAL_QUERIED`, reranking emits `RETRIEVAL_RERANKED`,
and each retrieved chunk arrives as a context item carrying provenance. The
pipeline is a context source, so what survives the budget is recorded too.

## Minimal example

```python
from rewyn.rag import Document, RAGPipeline

pipeline = RAGPipeline(top_k=3)
await pipeline.index_documents(
    [Document(text="Acme has paid every invoice on time since 2021.", title="Acme history")]
)

result = await pipeline.retrieve("How does Acme pay?")
print(result.texts[0])
```

The default embedder is a deterministic hashing embedder. It needs no API
key and no model, which makes tests and demos reproducible. It is not a
semantic embedder; swap it for production.

## Production example

```python
from rewyn.context import Context, TrustLevel
from rewyn.rag import LexicalReranker, OpenAIEmbedder, ParagraphChunker, RAGPipeline

pipeline = RAGPipeline(
    name="internal-docs",
    version="7",
    chunker=ParagraphChunker(max_chars=1200),
    embedder=OpenAIEmbedder("text-embedding-3-large"),
    reranker=LexicalReranker(),
    top_k=5,
    trust_level=TrustLevel.INTERNAL,
)

await pipeline.index_documents(documents)

context = Context([pipeline.as_context_source()], budget=8000)
agent = Agent(model=..., context=context)
```

`version` on the pipeline is recorded as a dependency of every run. When
answers change after a re-index, the behavior manifest shows the retriever
version moved.

### Provenance

```python
result = await pipeline.retrieve("Acme payment terms")
for item in result.context:
    print(item.provenance.source, item.provenance.version, item.provenance.hash)
```

## API reference

`rewyn/rag/pipeline.py` for `RAGPipeline` and `RetrievalResult`.
`rewyn/rag/chunker.py` for `FixedSizeChunker` and `ParagraphChunker`.
`rewyn/rag/embeddings.py` for `HashingEmbedder` and `OpenAIEmbedder`.
`rewyn/rag/retriever.py` for `VectorRetriever`, `KeywordRetriever` and
`InMemoryVectorIndex`.
`rewyn/rag/reranker.py` for `LexicalReranker` and `ModelReranker`.
`rewyn/integrations/search.py` for Elasticsearch and OpenSearch,
`rewyn/integrations/pgvector.py` for PostgreSQL vector search.

## Failure modes

**Retrieval returns irrelevant chunks.** With the default hashing embedder
this is expected: it matches tokens, not meaning. Use a real embedder.

**The right chunk was retrieved and the model still missed it.** Check the
`CONTEXT_ASSEMBLED` event. The chunk may have been excluded by the budget.
`assembled.decision.render()` names it.

**Chunks are cut mid-sentence.** `FixedSizeChunker` snaps to whitespace but
not to sentences. `ParagraphChunker` respects structure.

**The index is empty.** `InMemoryVectorIndex` does not persist. Re-index on
startup, or supply a real one:

```python
from rewyn.integrations.pgvector import PgVectorIndex
from rewyn.integrations.search import SearchVectorIndex

RAGPipeline(index=PgVectorIndex(connection, dimension=1536))
RAGPipeline(index=SearchVectorIndex("chunks", url="http://opensearch:9200"))
```

**A retrieved document contains an instruction.** Retrieved content is data.
Set `trust_level` on the pipeline so it is rendered as untrusted where
appropriate. See [Security](security.md).
