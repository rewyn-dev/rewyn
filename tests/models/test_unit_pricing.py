"""Cost engineering beyond tokens (spec §40).

Tool, embedding, retrieval and sandbox costs are all in the spec's list, and
none of them are priced per token.
"""

from __future__ import annotations

import pytest

from rewyn import Agent, tool
from rewyn.core.run import start_run
from rewyn.models.pricing import (
    UnitPrice,
    clear_unit_prices,
    compute_unit_cost,
    lookup_unit_price,
    register_unit_price,
)
from rewyn.rag import Document, RAGPipeline
from rewyn.rag.embeddings import embedding_units
from rewyn.runtime import create
from rewyn.testing import FakeModel


@pytest.fixture(autouse=True)
def clean_prices():
    clear_unit_prices()
    yield
    clear_unit_prices()


@tool
def bureau_score(account: str) -> int:
    """Fetch a bureau score."""
    return 771


def test_an_unpriced_operation_costs_nothing():
    assert compute_unit_cost("tool", "anything") == 0.0
    assert lookup_unit_price("tool", "anything") is None


def test_the_three_price_shapes_combine():
    register_unit_price(
        "sandbox", "box", UnitPrice(per_call=0.01, per_second=0.002, per_million_units=1.0)
    )
    cost = compute_unit_cost("sandbox", "box", calls=2, seconds=5.0, units=1_000_000)
    assert cost == pytest.approx(0.02 + 0.01 + 1.0)


def test_the_longest_prefix_wins():
    register_unit_price("tool", "salesforce_", UnitPrice(per_call=0.001))
    register_unit_price("tool", "salesforce_refund", UnitPrice(per_call=0.05))
    assert compute_unit_cost("tool", "salesforce_lookup") == pytest.approx(0.001)
    assert compute_unit_cost("tool", "salesforce_refund") == pytest.approx(0.05)


def test_categories_do_not_bleed_into_each_other():
    register_unit_price("tool", "shared", UnitPrice(per_call=0.01))
    assert compute_unit_cost("retrieval", "shared") == 0.0


async def test_a_tool_call_records_tool_cost():
    register_unit_price("tool", "bureau_score", UnitPrice(per_call=0.0025))
    model = FakeModel([FakeModel.tool_call("bureau_score", {"account": "A"}), "771"])
    async with start_run("costed", record=False) as run:
        await Agent(model, tools=[bureau_score], name="c").arun("score?")
    assert run.manifest.cost.tool == pytest.approx(0.0025)
    assert run.manifest.cost.total > run.manifest.cost.model


async def test_a_tool_can_declare_its_own_price():
    @tool(cost_per_call=0.01)
    def priced(x: str) -> str:
        """A tool that charges."""
        return x

    assert priced.price(0.0) == pytest.approx(0.01)
    model = FakeModel([FakeModel.tool_call("priced", {"x": "a"}), "done"])
    async with start_run("costed", record=False) as run:
        await Agent(model, tools=[priced], name="c").arun("go")
    assert run.manifest.cost.tool == pytest.approx(0.01)


def test_a_declared_price_is_not_part_of_the_tool_fingerprint():
    """Price is commercial metadata; changing it is not a behaviour change."""
    from rewyn.tools.tool import make_tool

    def fn(x: str) -> str:
        """A tool."""
        return x

    assert make_tool(fn).fingerprint() == make_tool(fn, cost_per_call=0.05).fingerprint()


async def test_indexing_and_retrieval_record_embedding_and_retrieval_cost():
    register_unit_price("embedding", "hashing", UnitPrice(per_million_units=0.13))
    register_unit_price("retrieval", "docs", UnitPrice(per_call=0.0001))

    async with start_run("rag", record=False) as run:
        pipeline = RAGPipeline(name="docs", top_k=2)
        await pipeline.index_documents([Document(text="Acme pays on time. " * 50, title="t")])
        indexed_cost = run.manifest.cost.embedding
        await pipeline.retrieve("Acme payments")

    assert indexed_cost > 0, "indexing embeds, so indexing costs"
    assert run.manifest.cost.embedding > indexed_cost, "the query is embedded too"
    assert run.manifest.cost.retrieval == pytest.approx(0.0001)


async def test_a_keyword_retriever_records_retrieval_cost_without_embedding():
    from rewyn.rag import Document, FixedSizeChunker, KeywordRetriever

    register_unit_price("retrieval", "keyword", UnitPrice(per_call=0.0002))
    retriever = KeywordRetriever()
    retriever.add(FixedSizeChunker().chunk(Document(text="acme invoices are paid on time")))
    async with start_run("kw", record=False) as run:
        await retriever.retrieve("acme")
    assert run.manifest.cost.retrieval == pytest.approx(0.0002)
    assert run.manifest.cost.embedding == 0.0


async def test_sandbox_time_is_charged():
    register_unit_price("sandbox", "subprocess", UnitPrice(per_second=0.5))
    async with start_run("box", record=False) as run:
        await create().aexecute("print(1)")
    assert run.manifest.cost.sandbox > 0


def test_embedding_units_scale_with_text():
    assert embedding_units(["a" * 400]) == 100
    assert embedding_units([]) == 1


async def test_the_run_total_is_the_sum_of_every_category():
    register_unit_price("tool", "bureau_score", UnitPrice(per_call=0.002))
    model = FakeModel([FakeModel.tool_call("bureau_score", {"account": "A"}), "771"])
    async with start_run("total", record=False) as run:
        await Agent(model, tools=[bureau_score], name="c").arun("score?")
    cost = run.manifest.cost
    assert cost.total == pytest.approx(
        cost.model + cost.tool + cost.embedding + cost.retrieval + cost.sandbox
    )


async def test_the_tool_event_carries_the_cost():
    from rewyn.core.event import EventType

    register_unit_price("tool", "bureau_score", UnitPrice(per_call=0.003))
    model = FakeModel([FakeModel.tool_call("bureau_score", {"account": "A"}), "771"])
    async with start_run("evented", record=False) as run:
        await Agent(model, tools=[bureau_score], name="c").arun("score?")
    returned = run.events_of(EventType.TOOL_RETURNED)[0]
    assert returned.payload["cost"] == pytest.approx(0.003)
