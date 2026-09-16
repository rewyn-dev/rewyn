"""Pricing tables and cost computation (spec §40).

Two tables. Model prices are USD per one million tokens. The bundled table covers the models
whose public list prices were verified when this file was last updated;
anything else yields a cost with ``source="unknown"`` rather than a
fabricated number. Applications register their own prices with
:func:`register_price`.

The second table prices everything that is not tokens: tool calls, embedding
batches, retrieval queries and sandbox time. Spec §40 requires all of them in
the run's cost breakdown, and none of them are per-token. See
:func:`register_unit_price`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.models.base import Cost, Usage

PRICING_TABLE_DATE = "2026-06-24"


@dataclass(frozen=True, slots=True)
class Price:
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float | None = None
    cache_write_per_million: float | None = None


_TABLE: dict[tuple[str, str], Price] = {}


def register_price(provider: str, model: str, price: Price) -> None:
    """Register (or override) the price for ``provider``/``model``.

    ``model`` may be an exact id or a prefix; the longest matching prefix wins.
    """
    _TABLE[(provider, model)] = price


def lookup_price(provider: str, model: str) -> Price | None:
    best: tuple[int, Price] | None = None
    for (prov, prefix), price in _TABLE.items():
        if (
            prov == provider
            and model.startswith(prefix)
            and (best is None or len(prefix) > best[0])
        ):
            best = (len(prefix), price)
    return best[1] if best else None


def compute_cost(provider: str, model: str, usage: Usage) -> Cost:
    from rewyn.models.base import Cost

    price = lookup_price(provider, model)
    if price is None:
        return Cost(source="unknown")
    cache_read_rate = (
        price.cache_read_per_million
        if price.cache_read_per_million is not None
        else price.input_per_million * 0.1
    )
    cache_write_rate = (
        price.cache_write_per_million
        if price.cache_write_per_million is not None
        else price.input_per_million * 1.25
    )
    uncached_input = max(0, usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens)
    input_cost = (
        uncached_input * price.input_per_million
        + usage.cache_read_tokens * cache_read_rate
        + usage.cache_write_tokens * cache_write_rate
    ) / 1_000_000
    output_cost = usage.output_tokens * price.output_per_million / 1_000_000
    return Cost(
        input=round(input_cost, 8),
        output=round(output_cost, 8),
        total=round(input_cost + output_cost, 8),
        source="table",
    )


# Anthropic first-party list prices (verified 2026-06-24).
for _model, _price in {
    "claude-fable-5-1": Price(10.0, 50.0),
    "claude-fable-5": Price(10.0, 50.0),
    "claude-opus-5": Price(5.0, 25.0),
    "claude-opus-4-8": Price(5.0, 25.0),
    "claude-opus-4-7": Price(5.0, 25.0),
    "claude-opus-4-6": Price(5.0, 25.0),
    "claude-sonnet-5": Price(2.0, 10.0),
    "claude-sonnet-4-6": Price(3.0, 15.0),
    "claude-haiku-4-5": Price(1.0, 5.0),
}.items():
    register_price("anthropic", _model, _price)


@dataclass(frozen=True, slots=True)
class UnitPrice:
    """A price for something that is not measured in tokens (spec §40).

    Tool calls, embedding batches, retrieval queries and sandbox time all
    cost money, and none of them are priced per token. A unit price combines
    the three shapes those bills actually take.
    """

    per_call: float = 0.0
    per_second: float = 0.0
    per_million_units: float = 0.0
    currency: str = "USD"


CostCategory = Literal["tool", "embedding", "retrieval", "sandbox"]

_UNIT_TABLE: dict[tuple[str, str], UnitPrice] = {}


def register_unit_price(category: CostCategory, name: str, price: UnitPrice) -> None:
    """Price a tool, embedder, retriever or sandbox provider.

    ``name`` may be an exact name or a prefix; the longest match wins, so
    ``register_unit_price("tool", "salesforce_", ...)`` prices a whole server.
    """
    _UNIT_TABLE[(category, name)] = price


def lookup_unit_price(category: CostCategory, name: str) -> UnitPrice | None:
    best: tuple[int, UnitPrice] | None = None
    for (cat, prefix), price in _UNIT_TABLE.items():
        if cat == category and name.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), price)
    return best[1] if best else None


def compute_unit_cost(
    category: CostCategory,
    name: str,
    *,
    calls: int = 1,
    seconds: float = 0.0,
    units: int = 0,
) -> float:
    """Cost for one priced operation. Unpriced operations cost nothing."""
    price = lookup_unit_price(category, name)
    if price is None:
        return 0.0
    return (
        price.per_call * calls
        + price.per_second * seconds
        + price.per_million_units * units / 1_000_000
    )


def clear_unit_prices() -> None:
    """Drop every registered unit price. For tests."""
    _UNIT_TABLE.clear()
