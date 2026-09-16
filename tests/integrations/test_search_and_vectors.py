"""Elasticsearch retrieval and PostgreSQL vector search (spec §51)."""

from __future__ import annotations

from typing import Any

import pytest

from rewyn.core.event import EventType
from rewyn.core.run import start_run
from rewyn.rag import Document, FixedSizeChunker


def chunks(text: str, title: str = "doc") -> list[Any]:
    return FixedSizeChunker().chunk(Document(text=text, title=title))


class FakeElastic:
    """A tiny in-memory stand-in for the async Elasticsearch client."""

    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], dict[str, Any]] = {}
        self.queries: list[dict[str, Any]] = []

    async def index(self, *, index: str, id: str, document: dict[str, Any]) -> None:
        self.documents[(index, id)] = document

    async def search(self, *, index: str, size: int = 10, **query: Any) -> dict[str, Any]:
        self.queries.append({"index": index, "size": size, **query})
        hits = [
            {"_source": doc, "_score": 1.0 - position * 0.1}
            for position, ((idx, _), doc) in enumerate(self.documents.items())
            if idx == index
        ]
        return {"hits": {"hits": hits[:size]}}

    async def count(self, *, index: str) -> dict[str, int]:
        return {"count": sum(1 for idx, _ in self.documents if idx == index)}


async def test_the_search_retriever_indexes_and_retrieves():
    from rewyn.integrations.search import SearchRetriever

    client = FakeElastic()
    retriever = SearchRetriever("docs", client=client)
    await retriever.add(chunks("Acme pays every invoice on time."))

    async with start_run("search", record=False) as run:
        results = await retriever.retrieve("Acme invoices", top_k=3)

    assert results
    assert "Acme" in results[0].chunk.text
    assert [r.rank for r in results] == list(range(1, len(results) + 1))
    queried = run.events_of(EventType.RETRIEVAL_QUERIED)[0]
    assert queried.payload["retriever"] == "search"
    assert queried.payload["index"] == "docs"


async def test_the_search_retriever_records_retrieval_cost():
    from rewyn.integrations.search import SearchRetriever
    from rewyn.models.pricing import UnitPrice, clear_unit_prices, register_unit_price

    clear_unit_prices()
    register_unit_price("retrieval", "search", UnitPrice(per_call=0.0005))
    retriever = SearchRetriever("docs", client=FakeElastic())
    await retriever.add(chunks("some text"))
    async with start_run("cost", record=False) as run:
        await retriever.retrieve("text")
    assert run.manifest.cost.retrieval == pytest.approx(0.0005)
    clear_unit_prices()


async def test_a_named_search_retriever_reports_its_name():
    from rewyn.integrations.search import SearchRetriever

    retriever = SearchRetriever("docs", client=FakeElastic(), name="legal-corpus")
    assert retriever.name == "legal-corpus"


async def test_the_search_vector_index_stores_and_searches():
    from rewyn.integrations.search import SearchVectorIndex

    client = FakeElastic()
    index = SearchVectorIndex("vectors", client=client)
    pieces = chunks("Acme pays on time.")
    await index.add(pieces, [[0.1, 0.2, 0.3] for _ in pieces])

    assert await index.count() == len(pieces)
    found = await index.search([0.1, 0.2, 0.3], top_k=2)
    assert found
    assert found[0][0].text == pieces[0].text
    assert client.queries[-1]["knn"]["field"] == "vector"


async def test_the_search_vector_index_satisfies_the_protocol():
    from rewyn.integrations.search import SearchVectorIndex
    from rewyn.rag import VectorIndex

    assert isinstance(SearchVectorIndex("v", client=FakeElastic()), VectorIndex)


async def test_a_rag_pipeline_runs_on_a_search_backed_index():
    from rewyn.integrations.search import SearchVectorIndex
    from rewyn.rag import RAGPipeline

    pipeline = RAGPipeline(index=SearchVectorIndex("v", client=FakeElastic()), top_k=1)
    await pipeline.index_documents([Document(text="Acme pays on time.", title="a")])
    result = await pipeline.retrieve("Acme")
    assert result.final


# pgvector -----------------------------------------------------------------------
class FakePostgres:
    """An asyncpg-shaped connection that records SQL and returns fixed rows."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.rows = rows or []

    async def execute(self, sql: str, *args: Any) -> None:
        self.statements.append((sql, args))

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self.statements.append((sql, args))
        return self.rows


def test_pgvector_writes_an_upsert():
    import json

    from rewyn.core.sync import run_sync
    from rewyn.integrations.pgvector import PgVectorIndex

    connection = FakePostgres()
    index = PgVectorIndex(connection, dimension=3)
    pieces = chunks("Acme pays on time.")
    run_sync(index.add(pieces[:1], [[0.1, 0.2, 0.3]]))

    sql, args = connection.statements[0]
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert args[0] == pieces[0].id
    assert json.loads(args[2])["text"] == pieces[0].text
    assert args[3] == "[0.1,0.2,0.3]"


def test_pgvector_refuses_a_wrong_sized_embedding():
    from rewyn.core.sync import run_sync
    from rewyn.integrations.pgvector import PgVectorIndex

    index = PgVectorIndex(FakePostgres(), dimension=3)
    with pytest.raises(ValueError, match="2 dimensions"):
        run_sync(index.add(chunks("x")[:1], [[0.1, 0.2]]))


def test_pgvector_searches_and_counts():
    import json

    from rewyn.core.sync import run_sync
    from rewyn.integrations.pgvector import PgVectorIndex

    piece = chunks("Acme pays on time.")[0]
    rows = [(json.dumps(piece.model_dump(mode="json")), 0.93)]
    index = PgVectorIndex(FakePostgres(rows), dimension=3)

    found = run_sync(index.search([0.1, 0.2, 0.3], top_k=1))
    assert found[0][0].id == piece.id
    assert found[0][1] == pytest.approx(0.93)


def test_pgvector_metrics_pick_an_operator():
    from rewyn.integrations.pgvector import PgVectorIndex

    assert PgVectorIndex(FakePostgres(), metric="cosine")._operator == "<=>"
    assert PgVectorIndex(FakePostgres(), metric="l2")._operator == "<->"
    with pytest.raises(ValueError, match="unknown metric"):
        PgVectorIndex(FakePostgres(), metric="hamming")


def test_pgvector_refuses_an_unsafe_table_name():
    from rewyn.integrations.pgvector import PgVectorIndex

    with pytest.raises(ValueError, match="unsafe table name"):
        PgVectorIndex(FakePostgres(), table="chunks; DROP TABLE users")


def test_pgvector_drives_a_db_api_connection_with_repeated_placeholders():
    """`$1` appears twice in the search; positional binding must repeat it."""
    from rewyn.core.sync import run_sync
    from rewyn.integrations.pgvector import PgVectorIndex

    class DbApi:
        def __init__(self) -> None:
            self.executed: list[tuple[str, tuple[Any, ...]]] = []

        def cursor(self) -> Any:
            return self

        def execute(self, sql: str, args: tuple[Any, ...]) -> None:
            self.executed.append((sql, args))

        def fetchall(self) -> list[Any]:
            return []

    connection = DbApi()
    run_sync(PgVectorIndex(connection, dimension=3).search([0.1, 0.2, 0.3], top_k=2))
    sql, args = connection.executed[0]
    assert "$1" not in sql
    assert sql.count("%s") == 3
    assert list(args) == ["[0.1,0.2,0.3]", "[0.1,0.2,0.3]", 2]


def test_pgvector_emits_create_table_ddl():
    from rewyn.integrations.pgvector import PgVectorIndex

    ddl = PgVectorIndex(FakePostgres(), table="my_chunks", dimension=768).create_table_sql()
    assert "CREATE TABLE IF NOT EXISTS my_chunks" in ddl
    assert "vector(768)" in ddl
