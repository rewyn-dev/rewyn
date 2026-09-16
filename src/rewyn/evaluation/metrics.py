"""Deterministic evaluators (spec §29).

These need no model: they check the things that are objectively true or
false about a run -- did the output match, is it valid JSON, did it satisfy
a schema, were the right tools called, did it stay inside a latency or cost
budget. Each factory returns an :class:`~rewyn.evaluation.evaluator.Evaluator`
so deterministic and LLM-judged metrics compose in the same list.

``expected`` defaults to the dataset item's ``expected`` value, so the same
evaluator works across a whole dataset::

    evaluators = [exact_match(), json_valid(), max_cost(0.05)]
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from rewyn.core.schema import validate_json
from rewyn.core.types import JSONObject
from rewyn.evaluation.evaluator import Evaluator, Score, Subject


def _normalise(text: str, *, case_sensitive: bool, strip: bool) -> str:
    if strip:
        text = text.strip()
    return text if case_sensitive else text.lower()


def _expected_text(subject: Subject, expected: Any) -> str:
    value = subject.expected if expected is None else expected
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def exact_match(
    expected: Any = None, *, case_sensitive: bool = False, strip: bool = True
) -> Evaluator:
    """The output equals the expected text."""

    def check(subject: Subject) -> Score:
        raw = _expected_text(subject, expected)
        want = _normalise(raw, case_sensitive=case_sensitive, strip=strip)
        got = _normalise(subject.output, case_sensitive=case_sensitive, strip=strip)
        hit = bool(want) and want == got
        return Score(
            evaluator="exact_match",
            value=1.0 if hit else 0.0,
            passed=hit,
            reason="" if hit else f"expected {want!r}, got {got!r}",
            details={"expected": want, "actual": got},
        )

    return Evaluator(check, name="exact_match", description="Output equals the expected text.")


def contains(needle: str | None = None, *, case_sensitive: bool = False) -> Evaluator:
    """The output contains a substring."""

    def check(subject: Subject) -> Score:
        want = needle if needle is not None else _expected_text(subject, None)
        haystack = subject.output if case_sensitive else subject.output.lower()
        target = want if case_sensitive else want.lower()
        hit = bool(target) and target in haystack
        return Score(
            evaluator="contains",
            value=1.0 if hit else 0.0,
            passed=hit,
            reason="" if hit else f"{target!r} not found in output",
            details={"needle": target},
        )

    return Evaluator(check, name="contains", description="Output contains a substring.")


def regex_match(pattern: str, *, flags: int = 0, full: bool = False) -> Evaluator:
    """The output matches a regular expression."""
    compiled = re.compile(pattern, flags)

    def check(subject: Subject) -> Score:
        found = compiled.fullmatch(subject.output) if full else compiled.search(subject.output)
        return Score(
            evaluator="regex_match",
            value=1.0 if found else 0.0,
            passed=bool(found),
            reason="" if found else f"output does not match /{pattern}/",
            details={"pattern": pattern, "match": found.group(0) if found else None},
        )

    return Evaluator(check, name="regex_match", description="Output matches a regex.")


def json_valid() -> Evaluator:
    """The output parses as JSON (code fences and surrounding prose tolerated)."""

    def check(subject: Subject) -> Score:
        from rewyn.models.base import extract_json

        if subject.structured is not None:
            return Score(
                evaluator="json_valid", value=1.0, passed=True, details={"source": "structured"}
            )
        try:
            value, repaired = extract_json(subject.output)
        except ValueError as exc:
            return Score(evaluator="json_valid", value=0.0, passed=False, reason=str(exc))
        return Score(
            evaluator="json_valid",
            value=1.0,
            passed=True,
            reason="parsed after repair" if repaired else "",
            details={"repaired": repaired, "type": type(value).__name__},
        )

    return Evaluator(check, name="json_valid", description="Output is valid JSON.")


def schema_valid(schema: JSONObject) -> Evaluator:
    """The output satisfies a JSON Schema."""

    def check(subject: Subject) -> Score:
        from rewyn.models.base import extract_json

        data = subject.structured
        if data is None:
            try:
                data, _ = extract_json(subject.output)
            except ValueError as exc:
                return Score(evaluator="schema_valid", value=0.0, passed=False, reason=str(exc))
        errors = validate_json(schema, data)
        return Score(
            evaluator="schema_valid",
            value=0.0 if errors else 1.0,
            passed=not errors,
            reason="; ".join(errors[:3]),
            details={"errors": errors},
        )

    return Evaluator(check, name="schema_valid", description="Output satisfies a JSON Schema.")


def tool_called(name: str) -> Evaluator:
    """A named tool was called at least once."""

    def check(subject: Subject) -> Score:
        hit = subject.called(name)
        return Score(
            evaluator=f"tool_called:{name}",
            value=1.0 if hit else 0.0,
            passed=hit,
            reason="" if hit else f"tool {name!r} was never called",
            details={"calls": subject.tool_calls},
        )

    return Evaluator(check, name=f"tool_called:{name}", description=f"Tool {name!r} was called.")


def tool_correctness(
    expected_tools: Sequence[str] | None = None, *, ordered: bool = False
) -> Evaluator:
    """The set (or sequence) of tools called matches what was expected.

    Scores the fraction of expected tools that were actually used, so a
    partially correct trajectory is distinguishable from a wholly wrong one.
    """

    def check(subject: Subject) -> Score:
        want = list(expected_tools) if expected_tools is not None else _expected_tools(subject)
        got = list(subject.tool_calls)
        if not want:
            passed = not got
            return Score(
                evaluator="tool_correctness",
                value=1.0 if passed else 0.0,
                passed=passed,
                reason="" if passed else f"expected no tool calls, got {got}",
                details={"expected": want, "actual": got},
            )
        if ordered:
            hit = got[: len(want)] == want
            value = 1.0 if hit else 0.0
        else:
            matched = sum(1 for name in set(want) if name in got)
            value = matched / len(set(want))
            hit = value == 1.0
        missing = [name for name in want if name not in got]
        unexpected = [name for name in got if name not in want]
        return Score(
            evaluator="tool_correctness",
            value=value,
            passed=hit,
            reason="" if hit else f"missing {missing}, unexpected {unexpected}",
            details={"expected": want, "actual": got, "missing": missing, "unexpected": unexpected},
        )

    return Evaluator(
        check,
        name="tool_correctness",
        description="The tools called match the expected trajectory.",
        threshold=1.0,
    )


def _expected_tools(subject: Subject) -> list[str]:
    expected = subject.expected
    if isinstance(expected, dict):
        tools = expected.get("tools") or expected.get("tool_calls") or []
        return [str(t) for t in tools]
    if isinstance(expected, list):
        return [str(t) for t in expected]
    return []


def no_errors() -> Evaluator:
    """The run completed without an error and no tool returned an error."""

    def check(subject: Subject) -> Score:
        recorded = subject.recorded
        tool_errors = (
            [c.name for c in recorded.tool_calls if c.is_error] if recorded is not None else []
        )
        ok = subject.error is None and not tool_errors
        return Score(
            evaluator="no_errors",
            value=1.0 if ok else 0.0,
            passed=ok,
            reason=subject.error or (f"tool errors: {tool_errors}" if tool_errors else ""),
            details={"tool_errors": tool_errors},
        )

    return Evaluator(check, name="no_errors", description="The run produced no errors.")


def not_empty(min_length: int = 1) -> Evaluator:
    """The output is non-empty."""

    def check(subject: Subject) -> Score:
        length = len(subject.output.strip())
        ok = length >= min_length
        return Score(
            evaluator="not_empty",
            value=1.0 if ok else 0.0,
            passed=ok,
            reason="" if ok else f"output is {length} characters",
            details={"length": length},
        )

    return Evaluator(check, name="not_empty", description="Output is non-empty.")


def max_latency(milliseconds: float) -> Evaluator:
    """The run finished inside a latency budget."""

    def check(subject: Subject) -> Score:
        ok = subject.latency_ms <= milliseconds
        return Score(
            evaluator="max_latency",
            value=subject.latency_ms,
            passed=ok,
            reason="" if ok else f"{subject.latency_ms:.0f} ms exceeds {milliseconds:.0f} ms",
            details={"latency_ms": subject.latency_ms, "budget_ms": milliseconds},
        )

    return Evaluator(
        check,
        name="max_latency",
        description="Run latency is inside budget.",
        threshold=milliseconds,
        higher_is_better=False,
    )


def max_cost(usd: float) -> Evaluator:
    """The run finished inside a cost budget."""

    def check(subject: Subject) -> Score:
        ok = subject.cost <= usd
        return Score(
            evaluator="max_cost",
            value=subject.cost,
            passed=ok,
            reason="" if ok else f"${subject.cost:.6f} exceeds ${usd:.6f}",
            details={"cost": subject.cost, "budget": usd},
        )

    return Evaluator(
        check,
        name="max_cost",
        description="Run cost is inside budget.",
        threshold=usd,
        higher_is_better=False,
    )


DETERMINISTIC: tuple[str, ...] = (
    "contains",
    "exact_match",
    "json_valid",
    "max_cost",
    "max_latency",
    "no_errors",
    "not_empty",
    "regex_match",
    "schema_valid",
    "tool_called",
    "tool_correctness",
)
