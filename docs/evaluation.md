# Evaluation

## Concept

"It looks better" is not a result. Evaluation turns a judgement into a
number you can put a threshold on, and it belongs inside the SDK because the
thing being judged is a recorded run, not a string.

Three kinds, used together:

**Deterministic.** Exact match, regex, JSON validity, schema conformance,
tool trajectory, latency, cost. Cheap, fast and not arguable.

**LLM judged.** Correctness, relevance, groundedness, style, safety, task
completion. For qualities no assertion captures.

**Custom.** Your business definition of success, which nobody else can write
for you.

Every score emits `EVALUATION_SCORED`, so evaluations are as inspectable as
the runs they judge.

## Minimal example

```python
from rewyn.evaluation.evaluator import evaluator


@evaluator
def business_success(run):
    """Did the agent do the thing the business cares about?"""
    return "refund issued" in run.output


score = business_success(result)
print(score.passed, score.value)
```

An evaluator returns a bool, a float in `0..1`, a `(value, reason)` pair, or
a full `Score`.

## Production example

```python
from rewyn.evaluation.evaluator import evaluate, evaluator
from rewyn.evaluation.judges import Criterion, LLMJudge
from rewyn.evaluation.metrics import (
    exact_match,
    max_cost,
    no_errors,
    schema_valid,
    tool_correctness,
)


@evaluator(name="cites_policy", threshold=1.0, weight=2.0)
def cites_policy(subject) -> tuple[bool, str]:
    cited = "bureau score" in subject.output.lower()
    return cited, "" if cited else "the answer never references the score"


result = evaluate(
    recorded_run,
    [
        cites_policy,
        exact_match(),  # against the dataset expectation
        tool_correctness(["lookup_account", "bureau_score"]),
        schema_valid(Reply.model_json_schema()),
        no_errors(),
        max_cost(0.05),
        LLMJudge("anthropic:claude-opus-5", Criterion.GROUNDEDNESS, threshold=0.8),
    ],
)

print(result.passed, result.value)
for failure in result.failures():
    print(failure.evaluator, failure.reason)
```

An evaluator receives a `Subject`: the output, the input, the dataset
expectation, the tool calls, the cost, the latency and the recorded run
itself.

### Judges

A judge is a normal Rewyn model, so the judging call is recorded, costed
and replayable. It is not a blind spot.

```python
from rewyn.evaluation.judges import judge_panel

panel = judge_panel(
    "anthropic:claude-opus-5",
    (Criterion.CORRECTNESS, Criterion.GROUNDEDNESS, Criterion.SAFETY),
)
```

Judges score 1 to 5 and normalise to `0..1`, so `threshold=0.7` is 4 out of
5.

```bash
rewyn eval latest
rewyn eval latest -e myproject.evals:panel
```

## API reference

`rewyn/evaluation/evaluator.py` for `evaluator`, `Evaluator`, `Score`,
`Subject`, `evaluate` and `aevaluate`.
`rewyn/evaluation/metrics.py` for the deterministic metrics.
`rewyn/evaluation/judges.py` for `LLMJudge`, `Criterion` and `judge_panel`.

## Failure modes

**An evaluator raises.** It becomes a failing score with the error attached,
not a crashed evaluation run. Check `score.error`.

**The judge always says 5.** Judges are lenient by default; the built-in
prompt pushes back on this, but a judge sharing a model and a prompt style
with the thing it judges will agree with it. Use a different model, and
calibrate against examples you have graded yourself.

**`exact_match` never passes.** It compares against `subject.expected`,
which comes from the dataset item. Without a dataset, pass the expectation
explicitly.

**Scores are not comparable across runs.** A metric's `value` means whatever
the evaluator returns. Budget metrics like `max_cost` return the raw cost,
not a ratio. Read `passed` for verdicts and `mean` for trends within one
metric.

**Evaluation is slow.** Judges are model calls, one per case per judge. Use
deterministic metrics for what they can cover, and reserve judges for what
they cannot.
