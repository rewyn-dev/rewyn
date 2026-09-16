"""Modular, observable retrieval-augmented generation."""

from rewyn.rag.chunker import Chunk, Chunker, Document, FixedSizeChunker, ParagraphChunker
from rewyn.rag.embeddings import Embedder, HashingEmbedder, OpenAIEmbedder, cosine
from rewyn.rag.pipeline import RAGPipeline, RetrievalResult
from rewyn.rag.reranker import LexicalReranker, ModelReranker, Reranker
from rewyn.rag.retriever import (
    InMemoryVectorIndex,
    KeywordRetriever,
    Retrieved,
    Retriever,
    VectorIndex,
    VectorRetriever,
)

__all__ = [
    "Chunk",
    "Chunker",
    "Document",
    "Embedder",
    "FixedSizeChunker",
    "HashingEmbedder",
    "InMemoryVectorIndex",
    "KeywordRetriever",
    "LexicalReranker",
    "ModelReranker",
    "OpenAIEmbedder",
    "ParagraphChunker",
    "RAGPipeline",
    "Reranker",
    "RetrievalResult",
    "Retrieved",
    "Retriever",
    "VectorIndex",
    "VectorRetriever",
    "cosine",
]
