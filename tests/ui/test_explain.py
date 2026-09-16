"""AI-assisted debugging: prose that cannot outrun its evidence (UI spec §23)."""

from __future__ import annotations

import json

import pytest

from rewyn.testing import FakeModel
from rewyn.ui import explain
from rewyn.ui import schemas as s


def diff_view(**kwargs: object) -> s.DiffView:
    base: dict[str, object] = {
        "run_a": "run_a",
        "run_b": "run_b",
        "summary_a": {
            "id": "run_a",
            "name": "refund",
            "status": "succeeded",
            "started_at": "2026-01-01T00:00:00Z",
        },
        "summary_b": {
            "id": "run_b",
            "name": "refund",
            "status": "succeeded",
            "started_at": "2026-01-02T00:00:00Z",
        },
        "identical": False,
        "output_changed": True,
        "differences": [
            {
                "dimension": "context",
                "field": "refund-policy",
                "kind": "changed",
                "before": "v18",
                "after": "v19",
                "description": "retrieved refund policy changed from v18 to v19",
            },
            {
                "dimension": "output",
                "field": "output",
                "kind": "changed",
                "description": "the output changed",
            },
        ],
        "explanations": [
            {
                "observed": "output",
                "cause": "context",
                "confidence": 0.8,
                "rationale": "the retrieved policy is the only input that moved",
                "description": "the context change is associated with the changed output",
            }
        ],
    }
    base.update(kwargs)
    return s.DiffView.model_validate(base)


def test_the_ledger_puts_inputs_before_outcomes_and_numbers_everything():
    entries = explain.ledger(diff_view())
    assert [kind for _, kind, _ in entries] == ["input", "outcome", "analysis"]
    assert [key for key, _, _ in entries] == ["d1", "d2", "e1"]
    assert "v18 to v19" in entries[0][2]


def test_without_a_model_the_console_still_explains_and_says_who_wrote_it():
    """Local-first: an explanation must not need an API key (UI §44)."""
    narrative = explain.from_rules(diff_view())
    assert narrative.author == "rules"
    assert narrative.grounded
    assert narrative.claims
    assert {c.label for c in narrative.claims} == {"Observed", "Inference"}
    assert all(c.evidence for c in narrative.claims)
    assert "REWYN_EXPLAIN_MODEL" in narrative.note


def test_when_nothing_moved_it_says_so_rather_than_inventing_a_cause():
    narrative = explain.from_rules(
        diff_view(
            differences=[
                {
                    "dimension": "output",
                    "field": "output",
                    "kind": "changed",
                    "description": "the output changed",
                }
            ],
            explanations=[],
        )
    )
    assert "nothing recorded about the inputs does" in narrative.summary
    assert not [c for c in narrative.claims if c.label == "Inference"]


def test_a_claim_citing_nothing_is_dropped_and_the_removal_is_visible():
    """UI §23: never claim certainty without evidence."""
    entries = explain.ledger(diff_view())
    payload = {
        "summary": "the policy changed",
        "claims": [
            {"label": "Observed", "text": "the policy moved v18 to v19", "evidence": ["d1"]},
            {"label": "Inference", "text": "the vendor rotated their API key", "evidence": []},
            {"label": "Inference", "text": "invented", "evidence": ["d99"]},
        ],
    }
    claims, dropped = explain.ground(payload, diff_view(), entries)
    assert [c.text for c in claims] == ["the policy moved v18 to v19"]
    assert dropped == ["the vendor rotated their API key", "invented"]


async def test_the_explanation_agent_is_itself_a_recorded_run():
    """SDK §58: the product's claim, applied to the product."""
    answer = json.dumps(
        {
            "summary": "The retrieved refund policy changed from v18 to v19.",
            "claims": [
                {
                    "label": "Observed",
                    "text": "The retrieved refund policy changed from v18 to v19.",
                    "evidence": ["d1"],
                },
                {
                    "label": "Inference",
                    "text": "That change is associated with the changed output.",
                    "evidence": ["d1", "e1"],
                },
            ],
        }
    )
    model = FakeModel([answer])
    narrative = await explain.explain(diff_view(), model=model)

    assert narrative.author == "model"
    assert narrative.run_id is not None
    assert narrative.grounded
    assert [c.label for c in narrative.claims] == ["Observed", "Inference"]
    assert "v19" in narrative.summary

    from rewyn.storage.local import LocalStore

    manifest = LocalStore().read_manifest(narrative.run_id)
    assert manifest.name == "rewyn-explain"


async def test_the_model_is_given_the_ledger_and_nothing_else():
    model = FakeModel(
        ['{"summary": "x", "claims": [{"label": "Observed", "text": "x", "evidence": ["d1"]}]}']
    )
    await explain.explain(diff_view(), model=model)
    sent = "\n".join(message.text for request in model.requests for message in request.messages)
    assert "refund policy changed from v18 to v19" in sent
    assert "d1" in sent


async def test_an_answer_that_cites_nothing_falls_back_to_the_rules():
    model = FakeModel(
        [
            '{"summary": "it broke", "claims": ['
            '{"label": "Inference", "text": "the model got worse", "evidence": []}]}'
        ]
    )
    narrative = await explain.explain(diff_view(), model=model)
    assert narrative.author == "rules"
    assert narrative.dropped == ["the model got worse"]
    assert "discarded" in narrative.note


async def test_a_model_that_fails_does_not_leave_the_comparison_unexplained():
    """UI §48: the console answers, or says why it cannot."""
    from rewyn.models.base import ModelError

    model = FakeModel([ModelError("no credits")])
    narrative = await explain.explain(diff_view(), model=model)
    assert narrative.author == "rules"
    assert narrative.claims
    assert "could not run" in narrative.note


def test_the_model_is_opt_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("REWYN_EXPLAIN_MODEL", raising=False)
    assert explain.configured_model() is None
    monkeypatch.setenv("REWYN_EXPLAIN_MODEL", "anthropic:claude-opus-5")
    assert explain.configured_model() == "anthropic:claude-opus-5"
