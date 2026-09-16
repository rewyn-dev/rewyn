"""Documents and chunking (spec §11: Document → Parse → Chunk)."""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.types import JSONObject, new_id


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("doc"))
    text: str
    title: str | None = None
    source: str = "inline"
    version: str | None = None
    metadata: JSONObject = Field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str
    index: int
    text: str
    start: int
    end: int
    metadata: JSONObject = Field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]


@runtime_checkable
class Chunker(Protocol):
    name: str

    def chunk(self, document: Document) -> list[Chunk]: ...


def _chunk_metadata(document: Document) -> JSONObject:
    meta: JSONObject = {"source": document.source}
    if document.title:
        meta["title"] = document.title
    if document.version:
        meta["version"] = document.version
    meta.update(document.metadata)
    return meta


class FixedSizeChunker:
    """Character-window chunks with overlap, snapped to whitespace boundaries."""

    name = "fixed_size"

    def __init__(self, chunk_size: int = 1000, overlap: int = 100) -> None:
        if overlap >= chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, document: Document) -> list[Chunk]:
        text = document.text
        chunks: list[Chunk] = []
        start = 0
        index = 0
        while start < len(text):
            end = min(len(text), start + self.chunk_size)
            if end < len(text):
                boundary = text.rfind(" ", start + self.chunk_size // 2, end)
                if boundary > start:
                    end = boundary
            piece = text[start:end].strip()
            if piece:
                chunks.append(
                    Chunk(
                        id=f"{document.id}:{index}",
                        document_id=document.id,
                        index=index,
                        text=piece,
                        start=start,
                        end=end,
                        metadata=_chunk_metadata(document),
                    )
                )
                index += 1
            if end >= len(text):
                break
            start = max(end - self.overlap, start + 1)
        return chunks


class ParagraphChunker:
    """Split on blank lines, merging short paragraphs up to ``max_chars``."""

    name = "paragraph"

    def __init__(self, max_chars: int = 1200) -> None:
        self.max_chars = max_chars

    def chunk(self, document: Document) -> list[Chunk]:
        chunks: list[Chunk] = []
        buffer: list[str] = []
        buffer_start = 0
        cursor = 0
        index = 0

        def flush(end: int) -> None:
            nonlocal buffer, buffer_start, index
            piece = "\n\n".join(buffer).strip()
            if piece:
                chunks.append(
                    Chunk(
                        id=f"{document.id}:{index}",
                        document_id=document.id,
                        index=index,
                        text=piece,
                        start=buffer_start,
                        end=end,
                        metadata=_chunk_metadata(document),
                    )
                )
                index += 1
            buffer = []

        for paragraph in document.text.split("\n\n"):
            para_start = document.text.find(paragraph, cursor)
            cursor = para_start + len(paragraph)
            if buffer and sum(len(p) for p in buffer) + len(paragraph) > self.max_chars:
                flush(para_start)
            if not buffer:
                buffer_start = para_start
            buffer.append(paragraph)
        flush(len(document.text))
        return chunks
