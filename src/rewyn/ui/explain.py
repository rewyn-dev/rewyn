"""AI-assisted debugging: the narrative layer over a run comparison (UI spec §23).

UI §23 gives one example of the answer it wants and one rule about how to
write it. The example is a paragraph naming the retrieved policy that moved
from v18 to v19. The rule is: label Observed separately from Inference, and
never claim certainty without evidence.

So this module does not ask a model "why did these runs differ?". The
comparison already answered that, structurally, in
:func:`rewyn.ui.workspaces.diff_view`. What is missing is prose, and prose
is the one thing a model is unambiguously better at. The model is therefore
given a numbered ledger of what the diff observed and asked to write the
paragraph *out of that ledger*, citing an id for every sentence.

Two consequences, both deliberate:

- Every claim that cites nothing is dropped before the reader sees it, and
  named in ``dropped`` so the removal is visible rather than silent. A model
  that invents a cause does not get to publish it here.
- The explanation agent is a Rewyn agent. It runs through
  :meth:`~rewyn.agents.Agent.arun`, so it is recorded, replayable,
  costed and diffable exactly like the runs it is explaining -- which is the
  product's own claim, applied to itself (SDK §58).

With no model configured there is still an answer: the same ledger, rendered
by rule. Local-first means the console explains a difference on a laptop with
no API key, and says who wrote the explanation either way.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from typing import Any

from rewyn.ui import schemas as s

# How many observed differences the ledger carries. A diff can hold hundreds;
# a paragraph can carry a handful, and the ranked ones are the ones that matter.
MAX_EVIDENCE = 24

# Dimensions that describe the *outcome* rather than an input to it. An outcome
# is what needs explaining, so it is never offered as its own cause.
OUTCOME_DIMENSIONS = frozenset({"output", "cost", "latency", "quality", "status"})

SYSTEM = """\
You explain why two recorded AI executions differed, for an engineer reading \
a run comparison.

You are given a numbered ledger of differences that were actually observed \
between the two runs. That ledger is the only evidence that exists. You have \
no other knowledge of these runs.

Write a short explanation as JSON with:
  summary: one sentence naming the strongest input difference associated with \
the changed outcome.
  claims: a list, each with
    label: "Observed" for a restatement of ledger evidence, "Inference" for a \
statement about what caused what.
    text: one plain sentence. No markdown, no bullet characters.
    evidence: the ids from the ledger the sentence rests on, e.g. ["d3"].

Rules:
- Every claim must cite at least one ledger id. A sentence you cannot cite is \
a sentence you must not write.
- Label a claim "Observed" only when it restates ledger evidence without \
adding a causal link.
- Never assert certainty. An Inference says a difference is associated with \
the outcome, not that it caused it.
- Do not mention anything absent from the ledger: no model names, versions, \
policies or tools that are not in it.
- Four claims or fewer.
"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "enum": ["Observed", "Inference"]},
                    "text": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["label", "text", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "claims"],
    "additionalProperties": False,
}


def configured_model() -> str | None:
    """The model that writes explanations, if the developer named one.

    ``REWYN_EXPLAIN_MODEL`` is opt-in on purpose: an explanation costs a
    model call, and a console that silently spent money the first time
    somebody pressed "Explain difference" would be the wrong default.
    """
    value = os.environ.get("REWYN_EXPLAIN_MODEL", "").strip()
    return value or None


def ledger(diff: s.DiffView) -> list[tuple[str, str, str]]:
    """The evidence, numbered: ``(id, kind, sentence)``.

    Input differences come first and outcome differences last, because the
    outcome is the thing being explained. Rule-based explanations are carried
    too, as ``e1``, ``e2`` -- the model is allowed to agree with the analysis
    that already ran, and citing it is how it says so.
    """
    entries: list[tuple[str, str, str]] = []
    inputs = [d for d in diff.differences if d.dimension not in OUTCOME_DIMENSIONS]
    outcomes = [d for d in diff.differences if d.dimension in OUTCOME_DIMENSIONS]
    for index, difference in enumerate(inputs[:MAX_EVIDENCE], start=1):
        entries.append(
            (
                f"d{index}",
                "input",
                f"{difference.dimension}: {difference.description or difference.field}",
            )
        )
    for index, difference in enumerate(outcomes[: MAX_EVIDENCE // 2], start=len(entries) + 1):
        entries.append(
            (
                f"d{index}",
                "outcome",
                f"{difference.dimension}: {difference.description or difference.field}",
            )
        )
    for index, explanation in enumerate(diff.explanations, start=1):
        entries.append(
            (
                f"e{index}",
                "analysis",
                (
                    f"{explanation.description or explanation.rationale} "
                    f"(confidence {explanation.confidence:.2f})"
                ),
            )
        )
    return entries


def prompt(diff: s.DiffView, entries: Sequence[tuple[str, str, str]]) -> str:
    """The ledger, as the model sees it."""
    lines = [
        f"Run A: {diff.run_a}",
        f"Run B: {diff.run_b}",
        f"Output changed: {'yes' if diff.output_changed else 'no'}",
        "",
        "Ledger of observed differences:",
    ]
    lines.extend(f"  {key} [{kind}] {text}" for key, kind, text in entries)
    if not entries:
        lines.append("  (empty: the two runs are identical in every recorded dimension)")
    return "\n".join(lines)


def from_rules(diff: s.DiffView) -> s.NarrativeView:
    """The explanation the console can always give, with no model (UI §44).

    This is the same evidence the model would be handed, rendered directly.
    It is less fluent and exactly as honest.
    """
    entries = ledger(diff)
    claims: list[s.NarrativeClaim] = []
    inputs = [e for e in entries if e[1] == "input"]
    outcomes = [e for e in entries if e[1] == "outcome"]

    for key, _, text in inputs[:4]:
        claims.append(s.NarrativeClaim(label="Observed", text=f"{text}.", evidence=[key]))
    for key, _, text in outcomes[:2]:
        claims.append(s.NarrativeClaim(label="Observed", text=f"{text}.", evidence=[key]))
    for index, explanation in enumerate(diff.explanations[:3], start=1):
        claims.append(
            s.NarrativeClaim(
                label="Inference",
                text=explanation.description or explanation.rationale,
                evidence=[f"e{index}", *[k for k, _, _ in inputs[:1]]],
                confidence=explanation.confidence,
            )
        )

    if not entries:
        summary = "These two runs are identical in every dimension the comparison covers."
    elif inputs:
        summary = f"{inputs[0][2]} is the strongest observed difference between these runs" + (
            ", and the output changed." if diff.output_changed else "."
        )
    else:
        summary = (
            "The outcome differs, but nothing recorded about the inputs does — "
            "no model, prompt, context, tool or dependency changed."
        )
    return s.NarrativeView(
        run_a=diff.run_a,
        run_b=diff.run_b,
        author="rules",
        summary=summary,
        claims=claims,
        grounded=True,
        note=(
            "Composed from the comparison itself. Set REWYN_EXPLAIN_MODEL to have an "
            "explanation agent write it instead — the agent gets this same evidence and "
            "nothing else."
        ),
    )


def ground(
    payload: Any, diff: s.DiffView, entries: Sequence[tuple[str, str, str]]
) -> tuple[list[s.NarrativeClaim], list[str]]:
    """Keep the claims the ledger supports; return the rest so they can be shown as dropped."""
    known = {key for key, _, _ in entries}
    claims: list[s.NarrativeClaim] = []
    dropped: list[str] = []
    raw = payload.get("claims") if isinstance(payload, dict) else None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        cited = [c for c in _as_list(item.get("evidence")) if c in known]
        label = "Observed" if str(item.get("label")) == "Observed" else "Inference"
        if not cited:
            dropped.append(text)
            continue
        claims.append(s.NarrativeClaim(label=label, text=text, evidence=cited))
    return claims, dropped


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part for part in re.split(r"[\s,]+", value) if part]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _payload(result: Any) -> Any:
    if getattr(result, "structured", None) is not None:
        return result.structured
    try:
        return json.loads(str(getattr(result, "output", "") or ""))
    except (TypeError, ValueError):
        return None


async def explain(
    diff: s.DiffView,
    *,
    model: Any = None,
    tags: Sequence[str] = ("explanation",),
) -> s.NarrativeView:
    """Write the paragraph UI §23 asks for, and say who wrote it.

    Falls back to :func:`from_rules` whenever a model is not configured, is
    not reachable, or produces something the ledger does not support: an
    unexplained comparison is a worse outcome than a plainer explanation, and
    a wrong one is worse than both.
    """
    target = model if model is not None else configured_model()
    if target is None:
        return from_rules(diff)

    entries = ledger(diff)
    if not entries:
        return from_rules(diff)

    from rewyn.agents import Agent

    agent = Agent(
        target,
        name="rewyn-explain",
        instructions=SYSTEM,
        output_schema=SCHEMA,
        max_iterations=2,
        version="1",
    )
    try:
        result = await agent.arun(prompt(diff, entries), tags=tuple(tags))
    except Exception as exc:  # the console must still answer (UI §48)
        fallback = from_rules(diff)
        fallback.note = (
            f"The explanation agent could not run ({type(exc).__name__}), so this was "
            "composed from the comparison itself."
        )
        return fallback

    payload = _payload(result)
    claims, dropped = ground(payload, diff, entries)
    if not claims:
        fallback = from_rules(diff)
        fallback.run_id = result.run_id
        fallback.dropped = dropped
        fallback.note = (
            "The explanation agent cited no evidence from the comparison, so its answer "
            "was discarded and this was composed from the comparison itself."
        )
        return fallback

    summary = str(payload.get("summary") or "").strip() if isinstance(payload, dict) else ""
    return s.NarrativeView(
        run_a=diff.run_a,
        run_b=diff.run_b,
        author="model",
        model=getattr(getattr(agent, "model", None), "model", None) or str(target),
        run_id=result.run_id,
        summary=summary or claims[0].text,
        claims=claims,
        grounded=not dropped,
        dropped=dropped,
        note=(
            "Written by an explanation agent, from the comparison's evidence and nothing "
            "else. That agent is itself a recorded Rewyn run."
        ),
    )
