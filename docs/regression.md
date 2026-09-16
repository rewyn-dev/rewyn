# Regression

## Concept

The core loop of the product:

```
production run -> save as example -> dataset -> regression test -> release gate
```

A run that went well is a specification of what good looks like. A run that
went badly, once corrected, is a specification of what must not happen
again. Both become dataset items, and the dataset becomes the thing you run
before shipping.

What makes it a regression test rather than a benchmark is the baseline. The
question is not "is 94% good?", it is "was it 96% last week?".

## Minimal example

```python
from rewyn.evaluation.dataset import Dataset
from rewyn.evaluation.metrics import exact_match
from rewyn.evaluation.regression import run_regression

dataset = Dataset(name="support-critical")
dataset.add_run(run_id)  # a production run becomes a golden example
dataset.save()

report = run_regression(dataset, agent, evaluators=[exact_match()])
print(report.render())
```

`add_run` takes the run's input as the case input and, by default, its
output as the expectation.

## Production example

```python
from rewyn.evaluation.judges import Criterion, LLMJudge
from rewyn.evaluation.metrics import max_cost, no_errors, tool_correctness
from rewyn.evaluation.regression import ReleaseGate, Thresholds, run_regression

gate = ReleaseGate(
    "pre-deploy",
    Thresholds(
        min_success_rate=0.95,
        max_cost_per_run=0.05,
        max_avg_latency_ms=8000,
        min_metric={"judge:groundedness": 0.8},
        max_success_regression=0.02,  # may not fall two points below the baseline
        max_cost_increase=0.01,  # nor cost a cent more per run
    ),
)

report = run_regression(
    dataset,
    agent,
    evaluators=[
        business_success,
        tool_correctness(),
        no_errors(),
        max_cost(0.05),
        LLMJudge("anthropic:claude-opus-5", Criterion.GROUNDEDNESS),
    ],
    baseline="latest",  # compare against the last report for this dataset
    concurrency=8,
)

gate.evaluate(report)
print(report.render())
if not report.passed:
    raise SystemExit(1)
```

Rendered, that is the spec §31 summary:

```
Dataset: support-critical (v4)
Tests: 2,400

Success:
94.1% → 96.7%

judge:groundedness:
0.812 → 0.874  (97.1% pass)

Cost:
+0.000180/run ($0.031420)

Gates:
  PASS  success_rate: 0.967 >= 0.95
  PASS  cost_per_run: 0.03142 <= 0.05
  PASS  success_regression: 0 <= 0.02

Result:
PASS
```

Every case runs in its own recorded run, so a failing case can be inspected,
replayed and diffed like any production run.

### In CI

```bash
rewyn test support-critical --target myproject.agents:support --min-success 0.95
```

Exit code 1 on failure. That is the AI equivalent of a failing test suite.

### Datasets

```python
dataset.add(input="Refund for order 88", expected="Refunded $42.00", tags=["billing"])
dataset.bump()  # version 2
critical = dataset.filter(tags=["billing"])
```

Datasets are versioned and fingerprinted, and the report records both, so a
report always says which dataset it ran against.

```bash
rewyn datasets
rewyn datasets add support-critical --run latest
rewyn datasets show support-critical
```

## API reference

`rewyn/evaluation/dataset.py` for `Dataset` and `DatasetItem`.
`rewyn/evaluation/regression.py` for `run_regression`, `arun_regression`,
`RegressionReport`, `CaseResult`, `Thresholds`, `GateCheck` and
`ReleaseGate`.

## Failure modes

**`RegressionError: dataset has no items`.** `add_run` needs a recorded run.
Flush the recorder before building a dataset in a short script.

**Every case fails on `exact_match`.** Golden examples captured from a
non-deterministic model will not reproduce word for word. Use `contains`,
a schema check, or a judge for anything free-form; keep `exact_match` for
structured answers.

**The baseline is the run you just made.** `baseline="latest"` picks the
most recent saved report for that dataset, which is the previous one only if
you save in order. Pin a specific report id in CI.

**A gate passes because the metric was missing.** It does not. A threshold
on a metric no evaluator produced fails, with "metric was not produced by
this run" in the detail.

**Tests are slow.** Raise `concurrency`. Cases are independent; each gets
its own run and its own context.
