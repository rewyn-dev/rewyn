"""Live replay: re-executing recorded work against new components (spec §27).

Deterministic replay answers "what happened?". Live replay answers "what
would happen if I changed this?" -- a new model, a re-assembled context, a
fixed tool -- while everything you did not change still comes from the
recording.

Two building blocks live here. :func:`replay_prompts` re-issues each recorded
model request against a different model without needing the original
application code, which is what makes ``replay(run_id, model="new-model")``
work on its own. :func:`swapped_model` temporarily points an agent at a
different model so a full re-execution can be compared against its recording.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.types import JSONObject
from rewyn.models.base import Cost, Message, Model, Role, ToolSpec, Usage
from rewyn.replay.recorder import RecordedModelCall, RecordedRun

_REQUEST_OPTIONS = (
    "tool_choice",
    "temperature",
    "max_tokens",
    "top_p",
    "stop",
    "seed",
    "provider_options",
)


class PromptReplay(BaseModel):
    """One recorded model request re-issued against a different model."""

    model_config = ConfigDict(extra="forbid")

    index: int
    original_model: str
    replay_model: str
    original_text: str = ""
    replay_text: str = ""
    original_tool_calls: list[str] = Field(default_factory=list)
    replay_tool_calls: list[str] = Field(default_factory=list)
    original_usage: Usage = Field(default_factory=Usage)
    replay_usage: Usage = Field(default_factory=Usage)
    original_cost: float = 0.0
    replay_cost: float = 0.0
    original_latency_ms: float = 0.0
    replay_latency_ms: float = 0.0
    error: str | None = None

    @property
    def changed(self) -> bool:
        return (
            self.original_text != self.replay_text
            or self.original_tool_calls != self.replay_tool_calls
            or self.error is not None
        )


def request_options(call: RecordedModelCall) -> JSONObject:
    """The recorded generation options, ready to pass back to a model."""
    options: JSONObject = {}
    for key in _REQUEST_OPTIONS:
        value = call.request.get(key)
        if value not in (None, [], {}):
            options[key] = value
    return options


def recorded_tool_specs(call: RecordedModelCall) -> list[ToolSpec]:
    """Tool definitions exactly as they were offered to the model."""
    specs = call.request.get("tool_specs")
    if specs:
        return [ToolSpec.model_validate(s) for s in specs]
    return [ToolSpec(name=name) for name in call.tools]


def with_system(messages: Sequence[Message], instructions: str) -> list[Message]:
    """The recorded conversation with its system message replaced.

    Changing the instructions and leaving everything else recorded is the
    cleanest prompt experiment there is, so it gets a direct route rather
    than asking the caller to rebuild the transcript.
    """
    replaced = [m for m in messages if m.role is not Role.SYSTEM]
    return [Message.system(instructions), *replaced]


async def replay_prompts(
    recorded: RecordedRun,
    model: Model,
    *,
    indices: Sequence[int] | None = None,
    options: Mapping[str, Any] | None = None,
    system: str | None = None,
) -> list[PromptReplay]:
    """Re-issue each recorded model request against ``model``.

    This is a prompt-level experiment, not a re-run of the agent: every
    request is the one that was actually recorded, so a divergence in call
    *n* does not change the prompt of call *n + 1*. Use it to compare models
    over a real transcript; use a full re-execution when the loop itself
    needs to react to the new answers.

    ``options`` overrides recorded generation options (temperature, for
    example) and ``system`` replaces the system instructions, so the two
    things developers most often want to vary need no new plumbing.
    """
    wanted = set(indices) if indices is not None else None
    results: list[PromptReplay] = []
    for call in recorded.model_calls:
        if wanted is not None and call.index not in wanted:
            continue
        if call.response is None:
            continue
        merged = {**request_options(call), **dict(options or {})}
        entry = PromptReplay(
            index=call.index,
            original_model=f"{call.provider}:{call.model}",
            replay_model=f"{model.provider}:{model.name}",
            original_text=call.response.text,
            original_tool_calls=[c.name for c in call.response.tool_calls],
            original_usage=call.response.usage,
            original_cost=call.response.cost.total,
            original_latency_ms=call.latency_ms,
        )
        try:
            response = await model.agenerate(
                with_system(call.messages, system) if system is not None else call.messages,
                tools=recorded_tool_specs(call),
                output_schema=call.request.get("output_schema"),
                strict_output=False,
                **merged,
            )
        except Exception as exc:  # a failed experiment is a result, not a crash
            entry.error = f"{type(exc).__name__}: {exc}"
            results.append(entry)
            continue
        entry.replay_text = response.text
        entry.replay_tool_calls = [c.name for c in response.tool_calls]
        entry.replay_usage = response.usage
        entry.replay_cost = response.cost.total
        entry.replay_latency_ms = response.latency_ms
        results.append(entry)
    return results


def prompt_totals(entries: Sequence[PromptReplay]) -> tuple[Usage, Cost]:
    """Aggregate usage and cost across replayed prompts."""
    usage = Usage()
    total = 0.0
    for entry in entries:
        usage = usage + entry.replay_usage
        total += entry.replay_cost
    return usage, Cost(total=total)


@contextlib.contextmanager
def swapped_model(target: Any, model: Model | None) -> Iterator[None]:
    """Point an object's ``model`` attribute at ``model`` for the duration."""
    if model is None or not hasattr(target, "model"):
        yield
        return
    original = target.model
    target.model = model
    try:
        yield
    finally:
        target.model = original
