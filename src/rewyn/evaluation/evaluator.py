"""Evaluators (spec §29).

Evaluation is a first-class primitive, not a separate tool::

    @evaluator
    def business_success(run):
        return "refund issued" in run.output

An evaluator receives a :class:`Subject` -- the run under test together with
whatever the dataset expected -- and returns a bool, a float in ``0..1``, a
``(value, reason)`` pair, or a fully formed :class:`Score`. Every scoring
emits an ``EVALUATION_SCORED`` event, so evaluations are as replayable and
as diffable as the runs they judge.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol, overload, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import EventType
from rewyn.core.run import aensure_run
from rewyn.core.schema import fingerprint, to_jsonable
from rewyn.core.span import SpanKind
from rewyn.core.types import JSONObject, RewynError

EvaluatorFn = Callable[..., Any]
"""Any callable taking a :class:`Subject`; see :class:`Evaluator` for return shapes."""


class EvaluationError(RewynError):
    """An evaluator could not produce a score."""


class Score(BaseModel):
    """One evaluator's verdict on one subject."""

    model_config = ConfigDict(extra="forbid")

    evaluator: str
    value: float = 0.0
    passed: bool = False
    label: str | None = None
    reason: str = ""
    weight: float = 1.0
    threshold: float | None = None
    details: JSONObject = Field(default_factory=dict)
    duration_ms: float = 0.0
    error: str | None = None

    def to_payload(self) -> JSONObject:
        return self.model_dump(mode="json")


class Subject(BaseModel):
    """What an evaluator is handed: one execution and its expectation."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    output: str = ""
    input: Any = None
    expected: Any = None
    structured: Any = None
    metadata: JSONObject = Field(default_factory=dict)
    run_id: str | None = None
    error: str | None = None
    cost: float = 0.0
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[str] = Field(default_factory=list)
    tool_results: list[Any] = Field(default_factory=list)
    recorded: Any = Field(default=None, exclude=True, repr=False)

    # Convenience accessors used by evaluators ---------------------------------
    @property
    def text(self) -> str:
        return self.output

    @property
    def ok(self) -> bool:
        return self.error is None

    def called(self, tool_name: str) -> bool:
        return tool_name in self.tool_calls

    @classmethod
    def from_recorded(cls, recorded: Any, *, expected: Any = None, **extra: Any) -> Subject:
        """Build a subject from a :class:`~rewyn.replay.recorder.RecordedRun`."""
        manifest = recorded.manifest
        output = manifest.output
        return cls(
            output=output if isinstance(output, str) else to_jsonable_text(output),
            input=manifest.input,
            expected=expected,
            metadata=dict(manifest.metadata),
            run_id=manifest.id,
            error=manifest.error,
            cost=manifest.cost.total,
            latency_ms=manifest.duration_ms or 0.0,
            input_tokens=manifest.usage.input_tokens,
            output_tokens=manifest.usage.output_tokens,
            tool_calls=[c.name for c in recorded.tool_calls],
            tool_results=[c.result for c in recorded.tool_calls],
            recorded=recorded,
            **extra,
        )

    @classmethod
    def from_result(cls, result: Any, *, expected: Any = None, **extra: Any) -> Subject:
        """Build a subject from an agent :class:`~rewyn.agents.agent.RunResult`."""
        return cls(
            output=getattr(result, "output", "") or "",
            structured=getattr(result, "structured", None),
            expected=expected,
            run_id=getattr(result, "run_id", None),
            error=getattr(result, "error", None),
            cost=float(getattr(result, "cost", 0.0) or 0.0),
            input_tokens=getattr(getattr(result, "usage", None), "input_tokens", 0),
            output_tokens=getattr(getattr(result, "usage", None), "output_tokens", 0),
            **extra,
        )


def to_jsonable_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    import json

    return json.dumps(to_jsonable(value), ensure_ascii=False, sort_keys=True)


def coerce_subject(value: Any, *, expected: Any = None) -> Subject:
    """Accept a subject, a recorded run, an agent result or plain text."""
    if isinstance(value, Subject):
        return value
    if isinstance(value, str):
        return Subject(output=value, expected=expected)
    if hasattr(value, "model_calls") and hasattr(value, "manifest"):
        return Subject.from_recorded(value, expected=expected)
    if hasattr(value, "run_id") and hasattr(value, "output"):
        return Subject.from_result(value, expected=expected)
    if hasattr(value, "manifest") and hasattr(value, "events"):
        from rewyn.replay.recorder import RecordedRun

        return Subject.from_recorded(RecordedRun.from_run(value), expected=expected)
    raise EvaluationError(f"cannot evaluate {type(value).__name__}; pass a Subject")


class Evaluator:
    """A named, versioned scoring function."""

    def __init__(
        self,
        fn: EvaluatorFn,
        *,
        name: str | None = None,
        description: str = "",
        threshold: float = 0.5,
        weight: float = 1.0,
        version: str = "1",
        higher_is_better: bool = True,
        metadata: JSONObject | None = None,
    ) -> None:
        self.fn = fn
        self.name: str = name or str(getattr(fn, "__name__", "evaluator"))
        self.description = description or (inspect.getdoc(fn) or "").split("\n\n", 1)[0].strip()
        self.threshold = threshold
        self.weight = weight
        self.version = version
        self.higher_is_better = higher_is_better
        self.metadata = dict(metadata or {})

    def __repr__(self) -> str:
        return f"Evaluator({self.name!r}, threshold={self.threshold})"

    def fingerprint(self) -> str:
        return fingerprint(
            {
                "name": self.name,
                "version": self.version,
                "threshold": self.threshold,
                "higher_is_better": self.higher_is_better,
                "source": _source_of(self.fn),
            }
        )

    @property
    def dependency(self) -> Any:
        from rewyn.core.run import DependencyRef

        return DependencyRef(
            kind="evaluator",
            name=self.name,
            version=self.version,
            fingerprint=self.fingerprint(),
        )

    # Scoring ------------------------------------------------------------------
    async def ascore(self, subject: Any, *, expected: Any = None) -> Score:
        target = coerce_subject(subject, expected=expected)
        async with aensure_run("evaluation") as run:
            # An evaluation is a run like any other, and what it used is part
            # of its manifest (spec §34). It is also how a reader finds the
            # scores later without opening every event log.
            run.add_dependency(self.dependency)
            with run.span(f"evaluator:{self.name}", SpanKind.EVALUATION):
                started = time.perf_counter()
                try:
                    raw = self.fn(target)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    score = self._normalise(raw)
                except Exception as exc:
                    score = Score(
                        evaluator=self.name,
                        value=0.0,
                        passed=False,
                        error=f"{type(exc).__name__}: {exc}",
                        reason="evaluator raised",
                    )
                score.duration_ms = (time.perf_counter() - started) * 1000.0
                score.weight = self.weight
                score.threshold = self.threshold
                run.emit(
                    EventType.EVALUATION_SCORED,
                    {
                        "evaluator": self.name,
                        "version": self.version,
                        "fingerprint": self.fingerprint(),
                        "subject_run_id": target.run_id,
                        **score.to_payload(),
                    },
                )
                return score

    def score(self, subject: Any, *, expected: Any = None) -> Score:
        from rewyn.core.sync import run_sync

        return run_sync(self.ascore(subject, expected=expected))

    def __call__(self, subject: Any, *, expected: Any = None) -> Score:
        return self.score(subject, expected=expected)

    def _normalise(self, raw: Any) -> Score:
        reason = ""
        if isinstance(raw, Score):
            return raw.model_copy(update={"evaluator": raw.evaluator or self.name})
        if isinstance(raw, dict):
            data = {"evaluator": self.name, **raw}
            data.setdefault("passed", float(data.get("value", 0.0)) >= self.threshold)
            return Score.model_validate(data)
        if isinstance(raw, tuple) and len(raw) == 2:
            raw, reason = raw[0], str(raw[1])
        if raw is None:
            return Score(evaluator=self.name, value=0.0, passed=False, reason=reason or "no score")
        if isinstance(raw, bool):
            return Score(evaluator=self.name, value=1.0 if raw else 0.0, passed=raw, reason=reason)
        if isinstance(raw, int | float):
            value = float(raw)
            passed = value >= self.threshold if self.higher_is_better else value <= self.threshold
            return Score(evaluator=self.name, value=value, passed=passed, reason=reason)
        if isinstance(raw, str):
            return Score(evaluator=self.name, value=1.0, passed=True, label=raw, reason=reason)
        raise EvaluationError(f"evaluator {self.name!r} returned unsupported value {raw!r}")


def _source_of(fn: EvaluatorFn) -> str:
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):  # builtins, partials, lambdas defined in a REPL
        return repr(fn)


@overload
def evaluator(fn: EvaluatorFn, /) -> Evaluator: ...


@overload
def evaluator(
    *,
    name: str | None = None,
    description: str = "",
    threshold: float = 0.5,
    weight: float = 1.0,
    version: str = "1",
    higher_is_better: bool = True,
) -> Callable[[EvaluatorFn], Evaluator]: ...


def evaluator(fn: EvaluatorFn | None = None, /, **options: Any) -> Any:
    """Turn a function into an :class:`Evaluator` (usable bare or with options)."""
    if fn is not None:
        return Evaluator(fn, **options)

    def decorate(inner: EvaluatorFn) -> Evaluator:
        return Evaluator(inner, **options)

    return decorate


@runtime_checkable
class EvaluatorLike(Protocol):
    """Anything that can score a subject: an :class:`Evaluator` or an LLM judge."""

    name: str
    weight: float
    threshold: float

    def fingerprint(self) -> str: ...

    async def ascore(self, subject: Any, *, expected: Any = None) -> Score: ...


def as_evaluator(value: EvaluatorLike | EvaluatorFn) -> EvaluatorLike:
    """Accept an evaluator, a judge, or a bare function."""
    if isinstance(value, Evaluator) or hasattr(value, "ascore"):
        return value  # type: ignore[return-value]
    return Evaluator(value)


class EvaluationResult(BaseModel):
    """Every evaluator's score for one subject."""

    model_config = ConfigDict(extra="forbid")

    subject_run_id: str | None = None
    scores: list[Score] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.scores)

    @property
    def value(self) -> float:
        """Weighted mean of the scores."""
        total_weight = sum(s.weight for s in self.scores)
        if not total_weight:
            return 0.0
        return sum(s.value * s.weight for s in self.scores) / total_weight

    def by_name(self) -> dict[str, Score]:
        return {s.evaluator: s for s in self.scores}

    def failures(self) -> list[Score]:
        return [s for s in self.scores if not s.passed]


async def aevaluate(
    subject: Any,
    evaluators: Sequence[EvaluatorLike | EvaluatorFn],
    *,
    expected: Any = None,
) -> EvaluationResult:
    """Score one subject with every evaluator."""
    target = coerce_subject(subject, expected=expected)
    scores = [await as_evaluator(e).ascore(target) for e in evaluators]
    return EvaluationResult(subject_run_id=target.run_id, scores=scores)


def evaluate(
    subject: Any,
    evaluators: Sequence[EvaluatorLike | EvaluatorFn],
    *,
    expected: Any = None,
) -> EvaluationResult:
    """Synchronous facade over :func:`aevaluate`."""
    from rewyn.core.sync import run_sync

    return run_sync(aevaluate(subject, evaluators, expected=expected))
