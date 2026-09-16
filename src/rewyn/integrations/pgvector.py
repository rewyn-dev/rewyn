"""PostgreSQL vector search with pgvector (spec §51).

PostgreSQL is first on the spec's data list, and with the pgvector extension
it is also a perfectly good vector database. For most teams that is one less
system to run: the documents, the embeddings and the application data all
live in the database they already operate.

Implements the :class:`~rewyn.rag.retriever.VectorIndex` protocol, so it
drops into ``RAGPipeline(index=...)`` with nothing else changing::

    CREATE EXTENSION IF NOT EXISTS vector;

The connection is injected, because how you pool connections is your
decision, not this library's. Any DB-API or SQLAlchemy connection works.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from rewyn.rag.chunker import Chunk

DEFAULT_TABLE = "rewyn_chunks"


class PgVectorIndex:
    """A vector index stored in one PostgreSQL table."""

    name = "pgvector"

    def __init__(
        self,
        connection: Any,
        *,
        table: str = DEFAULT_TABLE,
        dimension: int = 1536,
        metric: str = "cosine",
    ) -> None:
        if metric not in {"cosine", "l2", "inner_product"}:
            raise ValueError(f"unknown metric {metric!r}")
        self.connection = connection
        self.table = _identifier(table)
        self.dimension = dimension
        self.metric = metric

    def __repr__(self) -> str:
        return f"PgVectorIndex(table={self.table!r}, dimension={self.dimension})"

    @property
    def _operator(self) -> str:
        return {"cosine": "<=>", "l2": "<->", "inner_product": "<#>"}[self.metric]

    # Schema -------------------------------------------------------------------
    def create_table_sql(self) -> str:
        """DDL for the backing table. Run it once, or manage it in your migrations."""
        return (
            f"CREATE TABLE IF NOT EXISTS {self.table} ("
            "  id TEXT PRIMARY KEY,"
            "  document_id TEXT NOT NULL,"
            "  chunk JSONB NOT NULL,"
            f"  embedding vector({self.dimension}) NOT NULL"
            ")"
        )

    async def create_table(self) -> None:
        await self._execute(self.create_table_sql())

    # VectorIndex --------------------------------------------------------------
    async def add(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        for chunk, vector in zip(chunks, vectors, strict=True):
            if len(vector) != self.dimension:
                raise ValueError(
                    f"embedding has {len(vector)} dimensions, table expects {self.dimension}"
                )
            await self._execute(
                f"INSERT INTO {self.table} (id, document_id, chunk, embedding) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (id) DO UPDATE SET chunk = EXCLUDED.chunk, "
                "embedding = EXCLUDED.embedding",
                chunk.id,
                chunk.document_id,
                json.dumps(chunk.model_dump(mode="json")),
                _vector_literal(vector),
            )

    async def search(self, vector: Sequence[float], *, top_k: int) -> list[tuple[Chunk, float]]:
        rows = await self._fetch(
            f"SELECT chunk, 1 - (embedding {self._operator} $1) AS score "
            f"FROM {self.table} ORDER BY embedding {self._operator} $1 LIMIT $2",
            _vector_literal(vector),
            top_k,
        )
        return [(_chunk(row[0]), float(row[1])) for row in rows]

    async def count(self) -> int:
        rows = await self._fetch(f"SELECT count(*) FROM {self.table}")
        return int(rows[0][0]) if rows else 0

    async def delete_document(self, document_id: str) -> None:
        await self._execute(f"DELETE FROM {self.table} WHERE document_id = $1", document_id)

    # Connection plumbing ------------------------------------------------------
    async def _execute(self, sql: str, *args: Any) -> None:
        await self._call("execute", sql, *args)

    async def _fetch(self, sql: str, *args: Any) -> list[Any]:
        result = await self._call("fetch", sql, *args)
        return list(result or [])

    async def _call(self, kind: str, sql: str, *args: Any) -> Any:
        """Drive either an asyncpg-style or a DB-API-style connection."""
        native = getattr(self.connection, kind, None)
        if callable(native):
            outcome = native(sql, *args)
            return await outcome if hasattr(outcome, "__await__") else outcome
        text, ordered = _to_paramstyle(sql, args)
        cursor = self.connection.cursor()
        cursor.execute(text, ordered)
        return cursor.fetchall() if kind == "fetch" else None


def _identifier(name: str) -> str:
    """Refuse anything that is not a plain table name.

    The table name is interpolated into SQL, so it must not be attacker
    controlled. Validating it here is cheaper than remembering to quote it at
    five call sites.
    """
    if not name.replace("_", "").replace(".", "").isalnum():
        raise ValueError(f"unsafe table name {name!r}")
    return name


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


_PLACEHOLDER = re.compile(r"\$(\d+)")


def _to_paramstyle(sql: str, args: Sequence[Any]) -> tuple[str, list[Any]]:
    """Rewrite ``$n`` placeholders to ``%s`` for DB-API drivers.

    Numbered placeholders can repeat and positional ones cannot, so the
    arguments are expanded to match the order they now appear in. Getting
    this wrong would bind the wrong value rather than fail loudly.
    """
    ordered: list[Any] = []

    def substitute(match: re.Match[str]) -> str:
        ordered.append(args[int(match.group(1)) - 1])
        return "%s"

    return _PLACEHOLDER.sub(substitute, sql), ordered


def _chunk(value: Any) -> Chunk:
    return Chunk.model_validate(json.loads(value) if isinstance(value, str) else value)
