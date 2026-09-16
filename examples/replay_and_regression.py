"""The v0.4 loop: record, replay, diff, capture a dataset, regression test.

Runs entirely offline against ``FakeModel``, so it needs no API key and no
account. Run with ``python examples/replay_and_regression.py``.

The shape is the one the product is built around:

    production run -> save as example -> dataset -> regression test -> gate
"""

from __future__ import annotations

from rewyn import Agent, tool
from rewyn.core.manifest import BehaviorManifest, detect_run_drift
from rewyn.evaluation.dataset import Dataset
from rewyn.evaluation.metrics import exact_match, max_cost, not_empty
from rewyn.evaluation.regression import ReleaseGate, Thresholds, run_regression
from rewyn.replay.diff import Dimension, diff
from rewyn.replay.replay import replay
from rewyn.runtime.recorder import default_recorder
from rewyn.testing import FakeModel

RATES = {"EUR": 1.09, "GBP": 1.27}


@tool
def exchange_rate(currency: str) -> float:
    """Return the US dollar value of one unit of ``currency``."""
    return RATES.get(currency.upper(), 0.0)


def build_agent(model: FakeModel) -> Agent:
    return Agent(
        model,
        name="fx-desk",
        instructions="Answer with the rate and nothing else.",
        tools=[exchange_rate],
        max_iterations=4,
    )


def scripted(answer: str) -> FakeModel:
    return FakeModel(
        [FakeModel.tool_call("exchange_rate", {"currency": "EUR"}), answer], name="fx-1"
    )


def main() -> None:
    # 1. A production run.
    original = build_agent(scripted("1.09")).run("What is EUR in USD?")
    default_recorder().flush()
    print(f"run {original.run_id}: {original.output!r}")

    # 2. Deterministic replay: the recording answers every model and tool call,
    #    so the provider is never touched and the output is reproduced exactly.
    starved = build_agent(FakeModel([], name="fx-1"))
    replayed = replay(original.run_id, target=starved)
    print(
        f"replay {replayed.run_id}: identical={replayed.identical} "
        f"faithful={replayed.faithful} "
        f"({replayed.substitutions} recorded responses substituted)"
    )

    # 3. A behaviour change, and the diff that finds it.
    changed = build_agent(scripted("The euro is worth about 1.09 dollars.")).run(
        "What is EUR in USD?"
    )
    default_recorder().flush()
    report = diff(original.run_id, changed.run_id, dimensions=[Dimension.OUTPUT, Dimension.COST])
    print("\ndifferences (facts)")
    for difference in report.differences:
        print(f"  {difference.describe()}")
    print("possible explanations (hypotheses)")
    for explanation in report.explanations[:2]:
        print(f"  {explanation.describe()}")

    # 4. Drift: did anything upstream actually change?
    drift = detect_run_drift(original.run_id, changed.run_id)
    print(f"\nsilent drift: {drift.silent_drift}")

    # 5. Save the good run as a golden example and regression test against it.
    dataset = Dataset(name="fx-critical", description="Rates the desk must not get wrong")
    dataset.add_run(original.run_id)
    dataset.save()

    gate = ReleaseGate("pre-deploy", Thresholds(min_success_rate=1.0, max_cost_per_run=0.01))
    result = run_regression(
        dataset,
        build_agent(FakeModel(["1.09"], cycle=True)),
        evaluators=[exact_match(), not_empty(), max_cost(0.01)],
    )
    print("\n" + gate.evaluate(result).render())

    # 6. The release manifest for everything the run depended on.
    manifest = BehaviorManifest.from_runs("fx-desk", [original.run_id], version="1.0.0")
    print("\n" + manifest.render())


if __name__ == "__main__":
    main()
