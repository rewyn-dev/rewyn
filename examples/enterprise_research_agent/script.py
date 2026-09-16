"""The scripted model responses that make this demo run offline and identically.

A real deployment passes ``"anthropic:claude-opus-5"`` instead. Keeping the
script in its own module makes the boundary obvious: nothing else in the
demo knows the model is fake.
"""

from __future__ import annotations

from rewyn.models.base import ModelRequest
from rewyn.models.pricing import Price, register_price
from rewyn.testing import FakeModel

# Frontier-model rates, so the cost figures in the demo are the shape of real
# ones. Prices match by prefix, so "research-1" picks up the "research" entry.
register_price("fake", "research", Price(3.0, 15.0))
register_price("fake", "analyst", Price(3.0, 15.0))
register_price("fake", "judge", Price(0.8, 4.0))

RESEARCH_SUMMARY = (
    "Account ACC-4417 (Acme Corporation), owned by Dana Whitfield, 6 year relationship. "
    "Current limit 100,000 USD with 38,400 outstanding and no open disputes. "
    "Bureau score 771, prime band. Committed annual spend 2,150,000 USD."
)

RECOMMENDATION = (
    "Recommendation: raise Acme Corporation's credit limit to 250,000 USD.\n\n"
    "Evidence. Payment history over 24 months shows 24 of 24 invoices paid, averaging "
    "19 days against 30 day terms. Outstanding balance is 38,400 against a 100,000 "
    "limit. The bureau score is 771, which is prime. There are no open disputes.\n\n"
    "Policy. A score of 750 or above permits a limit up to 250,000 on analyst approval. "
    "The customer asked for 900,000; that is above the policy ceiling and is declined. "
    "Recommending 250,000."
)

REVISED_RECOMMENDATION = (
    "Recommendation: raise Acme Corporation's credit limit to 150,000 USD.\n\n"
    "Evidence. 24 of 24 invoices paid on time over 24 months, 38,400 outstanding "
    "against a 100,000 limit, bureau score 771, no disputes.\n\n"
    "Policy. A prime score permits up to 250,000, but utilisation has been under 40% "
    "all year, so a staged increase to 150,000 carries the same upside with less "
    "exposure."
)


def researcher_script() -> FakeModel:
    """Two tool calls, then the evidence summary."""
    return FakeModel(
        [
            FakeModel.tool_call("salesforce_lookup_account", {"company": "Acme Corporation"}),
            FakeModel.tool_call("credit_bureau_score", {"account_id": "ACC-4417"}),
            RESEARCH_SUMMARY,
        ],
        name="research-1",
    )


def analyst_script(recommendation: str = RECOMMENDATION) -> FakeModel:
    """One answer, written as if the policy skill had been read."""
    return FakeModel([recommendation], name="analyst-1")


def judge_script() -> FakeModel:
    """A scripted grader for the LLM-judged part of the evaluation."""

    def verdict(_request: ModelRequest) -> str:
        return '{"score": 5, "reason": "Cites payment history, balance, score and disputes."}'

    return FakeModel([verdict], cycle=True, name="judge-1")
