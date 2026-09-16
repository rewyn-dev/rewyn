"""Agent lifecycle hooks: observe or intervene at each stage of a run."""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from rewyn.models.base import ModelResponse, ToolResultPart

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.agents.agent import Agent, RunResult
    from rewyn.agents.loop import LoopContext


class AgentHooks(Protocol):
    """All methods are optional; implement the ones you need."""

    async def on_start(self, agent: Agent, ctx: LoopContext) -> None: ...

    async def on_iteration(self, agent: Agent, ctx: LoopContext, done: bool) -> None: ...

    async def on_model_response(
        self, agent: Agent, ctx: LoopContext, response: ModelResponse
    ) -> None: ...

    async def on_tool_results(
        self, agent: Agent, ctx: LoopContext, results: Sequence[ToolResultPart]
    ) -> None: ...

    async def on_finish(self, agent: Agent, ctx: LoopContext, result: RunResult) -> None: ...

    async def on_error(self, agent: Agent, ctx: LoopContext, error: BaseException) -> None: ...


class HookRunner:
    """Dispatch lifecycle events to every registered hook object (missing methods are fine)."""

    def __init__(self, hooks: Iterable[Any] = ()) -> None:
        self.hooks = list(hooks)

    async def dispatch(self, method: str, *args: Any) -> None:
        for hook in self.hooks:
            fn = getattr(hook, method, None)
            if fn is None:
                continue
            value = fn(*args)
            if inspect.isawaitable(value):
                await value

    def __bool__(self) -> bool:
        return bool(self.hooks)


class LoggingHooks:
    """Append a line per lifecycle event to ``lines`` (handy for tests and debugging)."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    async def on_start(self, agent: Agent, ctx: LoopContext) -> None:
        self.lines.append(f"start {agent.name}")

    async def on_iteration(self, agent: Agent, ctx: LoopContext, done: bool) -> None:
        self.lines.append(f"iteration {ctx.iteration} done={done}")

    async def on_model_response(
        self, agent: Agent, ctx: LoopContext, response: ModelResponse
    ) -> None:
        self.lines.append(f"model {response.finish_reason}")

    async def on_tool_results(
        self, agent: Agent, ctx: LoopContext, results: Sequence[ToolResultPart]
    ) -> None:
        self.lines.append(f"tools {len(results)}")

    async def on_finish(self, agent: Agent, ctx: LoopContext, result: RunResult) -> None:
        self.lines.append(f"finish {result.stop_reason.value}")

    async def on_error(self, agent: Agent, ctx: LoopContext, error: BaseException) -> None:
        self.lines.append(f"error {type(error).__name__}")
