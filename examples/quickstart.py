"""Rewyn quickstart: an agent with one tool, fully recorded, running offline.

Run with ``python examples/quickstart.py``. Swap ``FakeModel`` for
``"anthropic:claude-opus-5"`` (with ``ANTHROPIC_API_KEY`` set) to use a real
provider; nothing else changes.
"""

from __future__ import annotations

from rewyn import Agent, tool
from rewyn.testing import FakeModel


@tool(risk_level="low")
def ev_market_share(region: str) -> dict[str, float]:
    """Return electric vehicle market share (percent of new car sales) for a region."""
    data = {"europe": 24.5, "china": 41.0, "usa": 9.8}
    return {"region": data.get(region.lower(), 0.0)}


def main() -> None:
    model = FakeModel(
        [
            FakeModel.tool_call("ev_market_share", {"region": "europe"}),
            FakeModel.tool_call("ev_market_share", {"region": "china"}),
            "Europe is at 24.5% EV share and China at 41.0%; China leads adoption.",
        ]
    )
    agent = Agent(model=model, name="ev-researcher", tools=[ev_market_share], max_iterations=5)
    result = agent.run("Compare EV adoption in Europe and China")
    print(result.output)
    print(f"run {result.run_id}: {result.iterations} iterations, {result.tool_calls} tool calls")
    print("inspect it with: rewyn inspect", result.run_id)


if __name__ == "__main__":
    main()
