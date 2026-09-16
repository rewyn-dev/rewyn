"""Agent loop engine (spec §15, §17).

The :class:`Agent` drives iterations, budgets and events; a
:class:`LoopStrategy` decides what one iteration does. ``react`` is the
built-in strategy: observe → reason (model call) → act (tools) → observe.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.run import Run
from rewyn.core.state import State
from rewyn.core.types import ConfigurationError
from rewyn.models.base import Message, ModelResponse, Usage

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.agents.agent import Agent


class StopReason(StrEnum):
    COMPLETED = "completed"
    MAX_ITERATIONS = "max_iterations"
    MAX_TOKENS = "max_tokens"
    MAX_COST = "max_cost"
    MAX_TIME = "max_time"
    CUSTOM = "custom"
    EVALUATOR = "evaluator"
    GUARDRAIL = "guardrail"
    HANDOFF = "handoff"
    ERROR = "error"
    CANCELLED = "cancelled"


class Loop(BaseModel):
    """Loop configuration: strategy plus budgets (spec §17, §50)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: str = "react"
    max_iterations: int = 10
    max_tokens: int | None = None
    max_cost: float | None = None
    max_time_seconds: float | None = None
    strategy_options: dict[str, Any] = Field(default_factory=dict)


class LoopContext:
    """Mutable state shared between the agent and the strategy during a run."""

    def __init__(self, run: Run, messages: list[Message], state: State) -> None:
        self.run = run
        self.messages = messages
        self.state = state
        self.iteration = 0
        self.usage = Usage()
        self.cost = 0.0
        self.started = time.monotonic()
        self.responses: list[ModelResponse] = []
        self.tool_calls = 0
        self.output: str = ""
        self.structured: Any = None
        self.scratch: dict[str, Any] = {}

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started

    @property
    def last_response(self) -> ModelResponse | None:
        return self.responses[-1] if self.responses else None

    def account(self, response: ModelResponse) -> None:
        self.responses.append(response)
        self.usage = self.usage + response.usage
        self.cost += response.cost.total


class LoopStrategy(Protocol):
    name: str

    async def step(self, agent: Agent, ctx: LoopContext) -> bool:
        """Run one iteration. Return True when the task is complete."""
        ...


StopCondition = Callable[[LoopContext], bool | Awaitable[bool]]


def check_budget(loop: Loop, ctx: LoopContext) -> StopReason | None:
    """Return the budget that has been exhausted, if any."""
    if ctx.iteration >= loop.max_iterations:
        return StopReason.MAX_ITERATIONS
    if loop.max_tokens is not None and ctx.usage.total_tokens >= loop.max_tokens:
        return StopReason.MAX_TOKENS
    if loop.max_cost is not None and ctx.cost >= loop.max_cost:
        return StopReason.MAX_COST
    if loop.max_time_seconds is not None and ctx.elapsed_seconds >= loop.max_time_seconds:
        return StopReason.MAX_TIME
    return None


class ReActLoop:
    """Observe → reason → act. Finishes when the model answers without tool calls."""

    name = "react"

    async def step(self, agent: Agent, ctx: LoopContext) -> bool:
        response = await agent.call_model(ctx)
        ctx.messages.append(response.message)
        if response.tool_calls:
            results = await agent.run_tools(ctx, response.tool_calls)
            ctx.messages.append(Message.tool(results))
            return False
        ctx.output = response.text
        ctx.structured = response.structured
        return True


_STRATEGIES: dict[str, Callable[[], LoopStrategy]] = {"react": ReActLoop}


def register_strategy(name: str, factory: Callable[[], LoopStrategy]) -> None:
    _STRATEGIES[name] = factory


def evaluator_stop(evaluate: Callable[[LoopContext], bool | Awaitable[bool]]) -> StopCondition:
    """Mark a stop condition as an evaluator so the loop reports ``StopReason.EVALUATOR``."""

    def _condition(ctx: LoopContext) -> bool | Awaitable[bool]:
        return evaluate(ctx)

    _condition.is_evaluator = True  # type: ignore[attr-defined]
    return _condition


def get_strategy(name: str) -> LoopStrategy:
    if name not in _STRATEGIES:
        import rewyn.agents.planner  # noqa: F401 - registers plan_execute/reflection
    try:
        return _STRATEGIES[name]()
    except KeyError:
        raise ConfigurationError(
            f"unknown loop strategy {name!r}; known: {sorted(_STRATEGIES)}"
        ) from None


def known_strategies() -> list[str]:
    return sorted(_STRATEGIES)
