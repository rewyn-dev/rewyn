"""AI regression testing and release gates (spec §31, §32).

``rewyn test`` runs a dataset of historical AI interactions against the
current code and answers one question: *did behaviour get worse?*

A report is only useful next to the one before it, so every report is saved,
fingerprinted against the dataset it ran on, and can be compared to a
baseline. Thresholds turn that comparison into a verdict, which is what makes
this the AI equivalent of a CI quality gate::

    Git commit -> regression tests -> evaluation -> cost checks -> PASS / FAIL
"""

from __future__ import annotations

import asyncio
import inspect
import json
import statistics
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.run import RunStatus, start_run
from rewyn.core.schema import fingerprint
from rewyn.core.settings import get_settings
from rewyn.core.sync import run_sync
from rewyn.core.types import JSONObject, RewynError, new_id, utcnow
from rewyn.evaluation.dataset import Dataset, DatasetItem
from rewyn.evaluation.evaluator import (
    EvaluatorFn,
    EvaluatorLike,
    Score,
    Subject,
    as_evaluator,
)

REPORT_SCHEMA_VERSION = "1"


class RegressionError(RewynError):
    """A regression run could not be completed."""


class CaseResult(BaseModel):
    """One dataset item, executed and scored."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    run_id: str | None = None
    input: Any = None
    expected: Any = None
    output: str = ""
    scores: list[Score] = Field(default_factory=list)
    cost: float = 0.0
    latency_ms: float = 0.0
    tool_calls: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(s.passed for s in self.scores)

    def score_for(self, evaluator: str) -> Score | None:
        return next((s for s in self.scores if s.evaluator == evaluator), None)


class MetricSummary(BaseModel):
    """One evaluator aggregated across the dataset."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: int = 0
    total: int = 0
    mean: float = 0.0
    threshold: float | None = None

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


class GateCheck(BaseModel):
    """One release-gate condition and whether the report satisfied it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    passed: bool
    actual: float
    limit: float
    comparison: str
    detail: str = ""

    def describe(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        return f"{verdict}  {self.name}: {self.actual:.4g} {self.comparison} {self.limit:.4g}"


class Thresholds(BaseModel):
    """The conditions a regression report must satisfy to pass."""

    model_config = ConfigDict(extra="forbid")

    min_success_rate: float | None = None
    max_cost_per_run: float | None = None
    max_total_cost: float | None = None
    max_avg_latency_ms: float | None = None
    min_metric: dict[str, float] = Field(default_factory=dict)
    max_metric: dict[str, float] = Field(default_factory=dict)
    max_success_regression: float | None = None
    """How far the success rate may fall below the baseline (0.02 = two points)."""

    max_cost_increase: float | None = None
    """How much average cost per run may rise above the baseline, in currency units."""

    def fingerprint(self) -> str:
        return fingerprint(self.model_dump(mode="json"))

    def check(self, report: RegressionReport) -> list[GateCheck]:
        checks: list[GateCheck] = []
        if self.min_success_rate is not None:
            checks.append(_gate("success_rate", report.success_rate, ">=", self.min_success_rate))
        if self.max_cost_per_run is not None:
            checks.append(_gate("cost_per_run", report.avg_cost, "<=", self.max_cost_per_run))
        if self.max_total_cost is not None:
            checks.append(_gate("total_cost", report.total_cost, "<=", self.max_total_cost))
        if self.max_avg_latency_ms is not None:
            checks.append(
                _gate("avg_latency_ms", report.avg_latency_ms, "<=", self.max_avg_latency_ms)
            )
        for name, minimum in sorted(self.min_metric.items()):
            metric = report.metric(name)
            actual = metric.mean if metric else 0.0
            checks.append(
                _gate(
                    f"metric:{name}",
                    actual,
                    ">=",
                    minimum,
                    detail="" if metric else "metric was not produced by this run",
                )
            )
        for name, maximum in sorted(self.max_metric.items()):
            metric = report.metric(name)
            if metric is None:
                # A ceiling on a metric nobody produced must not pass silently.
                checks.append(
                    GateCheck(
                        name=f"metric:{name}",
                        passed=False,
                        actual=0.0,
                        limit=maximum,
                        comparison="<=",
                        detail="metric was not produced by this run",
                    )
                )
                continue
            checks.append(_gate(f"metric:{name}", metric.mean, "<=", maximum))
        if self.max_success_regression is not None and report.baseline_success_rate is not None:
            drop = report.baseline_success_rate - report.success_rate
            checks.append(
                _gate(
                    "success_regression",
                    max(drop, 0.0),
                    "<=",
                    self.max_success_regression,
                    detail=f"baseline {report.baseline_success_rate:.1%}",
                )
            )
        if self.max_cost_increase is not None and report.baseline_avg_cost is not None:
            increase = report.avg_cost - report.baseline_avg_cost
            checks.append(
                _gate(
                    "cost_increase",
                    max(increase, 0.0),
                    "<=",
                    self.max_cost_increase,
                    detail=f"baseline ${report.baseline_avg_cost:.6f}/run",
                )
            )
        return checks


def _gate(name: str, actual: float, comparison: str, limit: float, detail: str = "") -> GateCheck:
    passed = actual >= limit if comparison == ">=" else actual <= limit
    return GateCheck(
        name=name,
        passed=passed,
        actual=float(actual),
        limit=float(limit),
        comparison=comparison,
        detail=detail,
    )


class RegressionReport(BaseModel):
    """The result of running a dataset, with an optional baseline comparison."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("report"))
    schema_version: str = REPORT_SCHEMA_VERSION
    dataset: str
    dataset_version: str = "1"
    dataset_fingerprint: str = ""
    target: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    cases: list[CaseResult] = Field(default_factory=list)
    metrics: list[MetricSummary] = Field(default_factory=list)
    gates: list[GateCheck] = Field(default_factory=list)
    thresholds: Thresholds | None = None
    baseline_id: str | None = None
    baseline_success_rate: float | None = None
    baseline_avg_cost: float | None = None
    baseline_metrics: dict[str, float] = Field(default_factory=dict)
    duration_ms: float = 0.0
    metadata: JSONObject = Field(default_factory=dict)

    # Aggregates ---------------------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def succeeded(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.total if self.total else 0.0

    @property
    def total_cost(self) -> float:
        return sum(c.cost for c in self.cases)

    @property
    def avg_cost(self) -> float:
        return self.total_cost / self.total if self.total else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return statistics.fmean(c.latency_ms for c in self.cases) if self.cases else 0.0

    @property
    def errors(self) -> list[CaseResult]:
        return [c for c in self.cases if c.error is not None]

    @property
    def failures(self) -> list[CaseResult]:
        return [c for c in self.cases if not c.passed]

    @property
    def passed(self) -> bool:
        """True when every gate passed. With no thresholds, every case must pass."""
        if self.gates:
            return all(g.passed for g in self.gates)
        return self.total > 0 and not self.failures

    def metric(self, name: str) -> MetricSummary | None:
        return next((m for m in self.metrics if m.name == name), None)

    def success_delta(self) -> float | None:
        if self.baseline_success_rate is None:
            return None
        return self.success_rate - self.baseline_success_rate

    def cost_delta(self) -> float | None:
        if self.baseline_avg_cost is None:
            return None
        return self.avg_cost - self.baseline_avg_cost

    # Presentation -------------------------------------------------------------
    def render(self) -> str:
        """The spec §31 summary, as plain text."""
        lines = [f"Dataset: {self.dataset} (v{self.dataset_version})", f"Tests: {self.total:,}"]
        if self.target:
            lines.append(f"Target: {self.target}")
        lines.append("")
        success = _trend(self.baseline_success_rate, self.success_rate, "percent")
        lines.append(f"Success:\n{success}")
        for metric in self.metrics:
            baseline = self.baseline_metrics.get(metric.name)
            trend = _trend(baseline, metric.mean, "ratio")
            lines.append(f"\n{metric.name}:\n{trend}  ({metric.pass_rate:.1%} pass)")
        cost_delta = self.cost_delta()
        if cost_delta is None:
            lines.append(f"\nCost:\n${self.avg_cost:.6f}/run")
        else:
            lines.append(f"\nCost:\n{cost_delta:+.6f}/run (${self.avg_cost:.6f})")
        if self.gates:
            lines.append("\nGates:")
            lines.extend(f"  {g.describe()}" for g in self.gates)
        if self.errors:
            lines.append(f"\nErrors: {len(self.errors)}")
        lines.append(f"\nResult:\n{'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)

    def summary(self) -> JSONObject:
        delta = self.success_delta()
        return {
            "id": self.id,
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "tests": self.total,
            "success_rate": round(self.success_rate, 4),
            "success_delta": None if delta is None else round(delta, 4),
            "avg_cost": round(self.avg_cost, 6),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "passed": self.passed,
        }

    # Storage ------------------------------------------------------------------
    @staticmethod
    def directory(home: Path | None = None) -> Path:
        return (home / "evaluations") if home is not None else get_settings().home / "evaluations"

    def save(self, *, home: Path | None = None) -> Path:
        from rewyn.security.redaction import default_redactor
        from rewyn.storage.local import atomic_write_text

        payload = default_redactor().redact(self.model_dump(mode="json"))
        path = RegressionReport.directory(home) / f"{self.id}.json"
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return path

    @classmethod
    def load(cls, report_id: str, *, home: Path | None = None) -> RegressionReport:
        from rewyn.core.migrations import migrate

        path = RegressionReport.directory(home) / f"{report_id}.json"
        if not path.exists():
            raise RegressionError(f"report {report_id!r} not found at {path}")
        record = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(migrate("report", record))

    @classmethod
    def history(
        cls, dataset: str | None = None, *, home: Path | None = None, limit: int | None = None
    ) -> list[RegressionReport]:
        """Saved reports, newest first, optionally filtered to one dataset."""
        directory = cls.directory(home)
        if not directory.exists():
            return []
        reports: list[RegressionReport] = []
        for path in directory.glob("*.json"):
            try:
                report = cls.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if dataset is not None and report.dataset != dataset:
                continue
            reports.append(report)
        reports.sort(key=lambda r: r.created_at, reverse=True)
        return reports[:limit] if limit else reports

    @classmethod
    def latest(cls, dataset: str, *, home: Path | None = None) -> RegressionReport | None:
        found = cls.history(dataset, home=home, limit=1)
        return found[0] if found else None

    def compare_to(self, baseline: RegressionReport) -> RegressionReport:
        """Attach a baseline's aggregates so deltas and gates can use them."""
        self.baseline_id = baseline.id
        self.baseline_success_rate = baseline.success_rate
        self.baseline_avg_cost = baseline.avg_cost
        self.baseline_metrics = {m.name: m.mean for m in baseline.metrics}
        if self.thresholds is not None:
            self.gates = self.thresholds.check(self)
        return self


def _trend(before: float | None, after: float, style: str) -> str:
    def render(value: float) -> str:
        return f"{value:.1%}" if style == "percent" else f"{value:.3f}"

    if before is None:
        return render(after)
    return f"{render(before)} → {render(after)}"


# Execution --------------------------------------------------------------------
async def _invoke(target: Any, payload: Any) -> Any:
    runner = getattr(target, "arun", None)
    if callable(runner):
        return await runner(payload)
    if callable(target):
        outcome = target(payload)
        return await outcome if inspect.isawaitable(outcome) else outcome
    raise RegressionError(f"target {target!r} is neither runnable nor callable")


def _target_name(target: Any) -> str:
    for attribute in ("name", "__name__"):
        found = getattr(target, attribute, None)
        if isinstance(found, str):
            return found
    return type(target).__name__


async def _run_case(
    item: DatasetItem,
    target: Any,
    evaluators: Sequence[EvaluatorLike],
    *,
    dataset: Dataset,
    tags: Sequence[str],
) -> CaseResult:
    from rewyn.replay.recorder import RecordedRun

    case = CaseResult(
        item_id=item.id, input=item.input, expected=item.expected, tags=list(item.tags)
    )
    run = start_run(
        f"eval:{dataset.name}:{item.id}",
        tags=("regression", dataset.name, *tags),
        metadata={"dataset": dataset.name, "dataset_version": dataset.version, "item": item.id},
    )
    outcome: Any = None
    try:
        async with run:
            run.add_dependency(dataset.dependency)
            run.manifest.input = item.input
            outcome = await _invoke(target, item.input)
            run.manifest.output = _output_of(outcome)
    except Exception as exc:
        case.error = f"{type(exc).__name__}: {exc}"
    case.run_id = run.id
    case.cost = run.manifest.cost.total
    case.latency_ms = run.manifest.duration_ms or 0.0
    recorded = RecordedRun.from_run(run)
    case.tool_calls = [c.name for c in recorded.tool_calls]
    case.output = _output_of(outcome)
    if case.error is None and run.manifest.status is RunStatus.FAILED:
        case.error = run.manifest.error
    subject = Subject.from_recorded(recorded, expected=item.expected)
    subject.output = case.output
    subject.structured = getattr(outcome, "structured", None)
    subject.error = case.error
    subject.metadata = {**subject.metadata, **item.metadata}
    for evaluator in evaluators:
        case.scores.append(await evaluator.ascore(subject))
    return case


def _output_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    found = getattr(value, "output", None)
    if isinstance(found, str):
        return found
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _summarise(
    cases: Sequence[CaseResult], evaluators: Sequence[EvaluatorLike]
) -> list[MetricSummary]:
    summaries: list[MetricSummary] = []
    for evaluator in evaluators:
        scores = [s for c in cases for s in c.scores if s.evaluator == evaluator.name]
        if not scores:
            continue
        summaries.append(
            MetricSummary(
                name=evaluator.name,
                passed=sum(1 for s in scores if s.passed),
                total=len(scores),
                mean=statistics.fmean(s.value for s in scores),
                threshold=evaluator.threshold,
            )
        )
    return summaries


def _resolve_baseline(
    baseline: str | RegressionReport | None, dataset: str, home: Path | None
) -> RegressionReport | None:
    if baseline is None:
        return None
    if isinstance(baseline, RegressionReport):
        return baseline
    if baseline == "latest":
        return RegressionReport.latest(dataset, home=home)
    return RegressionReport.load(baseline, home=home)


async def arun_regression(
    dataset: Dataset | str,
    target: Any,
    *,
    evaluators: Sequence[EvaluatorLike | EvaluatorFn] = (),
    thresholds: Thresholds | None = None,
    baseline: str | RegressionReport | None = None,
    concurrency: int = 1,
    tags: Sequence[str] = (),
    save: bool = True,
    home: Path | None = None,
) -> RegressionReport:
    """Run every dataset case against ``target`` and score it.

    Each case runs in its own recorded run, so a failing case can be
    inspected, replayed and diffed like any production run.
    """
    data = dataset if isinstance(dataset, Dataset) else Dataset.load(dataset, home=home)
    if not data.items:
        raise RegressionError(f"dataset {data.name!r} has no items")
    resolved = [as_evaluator(e) for e in evaluators]
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(item: DatasetItem) -> CaseResult:
        async with semaphore:
            return await _run_case(item, target, resolved, dataset=data, tags=tags)

    if concurrency <= 1:
        cases = [await _one(item) for item in data.items]
    else:
        cases = list(await asyncio.gather(*(_one(item) for item in data.items)))

    report = RegressionReport(
        dataset=data.name,
        dataset_version=data.version,
        dataset_fingerprint=data.fingerprint(),
        target=_target_name(target),
        cases=cases,
        metrics=_summarise(cases, resolved),
        thresholds=thresholds,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        metadata={"evaluators": {e.name: e.fingerprint() for e in resolved}},
    )
    previous = _resolve_baseline(baseline, data.name, home)
    if previous is not None:
        report.compare_to(previous)
    elif thresholds is not None:
        report.gates = thresholds.check(report)
    if save:
        report.save(home=home)
    return report


def run_regression(
    dataset: Dataset | str,
    target: Any,
    *,
    evaluators: Sequence[EvaluatorLike | EvaluatorFn] = (),
    thresholds: Thresholds | None = None,
    baseline: str | RegressionReport | None = None,
    concurrency: int = 1,
    tags: Sequence[str] = (),
    save: bool = True,
    home: Path | None = None,
) -> RegressionReport:
    """Synchronous facade over :func:`arun_regression`."""
    return run_sync(
        arun_regression(
            dataset,
            target,
            evaluators=evaluators,
            thresholds=thresholds,
            baseline=baseline,
            concurrency=concurrency,
            tags=tags,
            save=save,
            home=home,
        )
    )


class ReleaseGate:
    """A named set of thresholds applied to a regression report (spec §32).

    ::

        gate = ReleaseGate("pre-deploy", Thresholds(min_success_rate=0.95))
        report = run_regression(dataset, agent, evaluators=[...])
        if not gate.evaluate(report).passed:
            raise SystemExit(1)
    """

    def __init__(self, name: str, thresholds: Thresholds, *, version: str = "1") -> None:
        self.name = name
        self.thresholds = thresholds
        self.version = version

    def __repr__(self) -> str:
        return f"ReleaseGate({self.name!r})"

    def fingerprint(self) -> str:
        return fingerprint(
            {
                "name": self.name,
                "version": self.version,
                "thresholds": self.thresholds.fingerprint(),
            }
        )

    def evaluate(self, report: RegressionReport) -> RegressionReport:
        """Apply the gate to a report in place and return it."""
        report.thresholds = self.thresholds
        report.gates = self.thresholds.check(report)
        report.metadata = {**report.metadata, "gate": self.name, "gate_version": self.version}
        return report
