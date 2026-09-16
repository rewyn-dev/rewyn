"""The replay entry point (spec §27).

::

    replay(run_id, model="new-model", context="original", tools="recorded")

``replay`` resolves to one of three modes depending on what you give it.

``reconstruct``
    Nothing is overridden and no target is supplied, so the recording is
    reproduced exactly: the recorded events are re-emitted into a fresh run
    and the output is the recorded output. This is deterministic replay.

``prompt``
    A different model is supplied but no target. Every recorded model request
    is re-issued against the new model and the answers are compared. No
    application code is needed, and the recorded transcript is the prompt.

``execute``
    A target (an agent, a graph, or any callable) is supplied and really runs.
    Components in ``recorded`` mode are answered from the recording through
    :class:`~rewyn.replay.deterministic.ReplayHooks`; components in ``live``
    mode execute for real.
"""

from __future__ import annotations

import contextlib
import inspect
import time
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import EventType
from rewyn.core.run import start_run
from rewyn.core.sync import run_sync
from rewyn.core.types import JSONObject
from rewyn.models.base import Model, Usage
from rewyn.replay.deterministic import ComponentMode, Mismatch, ReplayHooks
from rewyn.replay.live import PromptReplay, prompt_totals, replay_prompts, swapped_model
from rewyn.replay.recorder import RecordedRun, ReplayError

ReplayTarget = Any
"""An agent, a graph, or a callable taking the recorded input."""

ModelSetting = str | Model
ContextSetting = str

_LIFECYCLE = frozenset(
    {
        EventType.RUN_STARTED,
        EventType.RUN_FINISHED,
        EventType.RUN_FAILED,
        EventType.RUN_CANCELLED,
    }
)


class ReplayMode(StrEnum):
    RECONSTRUCT = "reconstruct"
    PROMPT = "prompt"
    EXECUTE = "execute"


class ReplayResult(BaseModel):
    """What a replay produced, and how far it drifted from the recording."""

    model_config = ConfigDict(extra="forbid")

    original_run_id: str
    run_id: str
    mode: ReplayMode
    model_mode: str
    tool_mode: str
    context_mode: str
    output: Any = None
    original_output: Any = None
    substitutions: int = 0
    mismatches: list[Mismatch] = Field(default_factory=list)
    prompts: list[PromptReplay] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    cost: float = 0.0
    original_cost: float = 0.0
    duration_ms: float = 0.0
    error: str | None = None

    @property
    def identical(self) -> bool:
        """True when the replay produced the recorded output."""
        if self.error is not None:
            return False
        if self.mode is ReplayMode.PROMPT:
            return not any(p.changed for p in self.prompts)
        return bool(self.output == self.original_output)

    @property
    def faithful(self) -> bool:
        """True when the replay also followed the recording call for call.

        A replay can be ``identical`` without being ``faithful``: the output
        matched, but some request did not line up with the recording and was
        answered by position instead. The mismatches say which.
        """
        return self.identical and not self.mismatches

    @property
    def changed_prompts(self) -> list[PromptReplay]:
        return [p for p in self.prompts if p.changed]

    @property
    def cost_delta(self) -> float:
        return self.cost - self.original_cost

    def summary(self) -> JSONObject:
        return {
            "original_run_id": self.original_run_id,
            "run_id": self.run_id,
            "mode": self.mode.value,
            "identical": self.identical,
            "faithful": self.faithful,
            "substitutions": self.substitutions,
            "mismatches": len(self.mismatches),
            "cost_delta": round(self.cost_delta, 6),
        }


def _resolve_model(setting: ModelSetting) -> tuple[ComponentMode, Model | None]:
    """``"recorded"``/``"live"`` are modes; anything else names a replacement model."""
    if isinstance(setting, Model):
        return ComponentMode.LIVE, setting
    if setting in (ComponentMode.RECORDED.value, ComponentMode.LIVE.value):
        return ComponentMode(setting), None
    from rewyn.models.registry import resolve_model

    return ComponentMode.LIVE, resolve_model(setting)


def _resolve_context(setting: ContextSetting) -> str:
    if setting not in ("original", "live"):
        raise ReplayError(f"context must be 'original' or 'live', not {setting!r}")
    return setting


async def _invoke(target: ReplayTarget, payload: Any) -> Any:
    runner = getattr(target, "arun", None)
    if callable(runner):
        return await runner(payload)
    if callable(target):
        outcome = target(payload)
        return await outcome if inspect.isawaitable(outcome) else outcome
    raise ReplayError(f"replay target {target!r} is neither runnable nor callable")


def _output_of(value: Any) -> Any:
    for attribute in ("output", "text"):
        found = getattr(value, attribute, None)
        if isinstance(found, str):
            return found
    return value


@dataclass(slots=True)
class _Plan:
    """The resolved settings for one replay."""

    recorded: RecordedRun
    model_mode: ComponentMode
    replacement: Model | None
    tool_mode: ComponentMode
    context_mode: str
    strict: bool = False
    tags: tuple[str, ...] = ("replay",)
    input: Any = None
    started: float = 0.0

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0

    def base(self, mode: ReplayMode, run_id: str) -> JSONObject:
        return {
            "original_run_id": self.recorded.id,
            "run_id": run_id,
            "mode": mode,
            "model_mode": self.model_mode.value,
            "tool_mode": self.tool_mode.value,
            "context_mode": self.context_mode,
            "original_output": self.recorded.output,
            "original_cost": self.recorded.manifest.cost.total,
            "duration_ms": self.elapsed_ms(),
        }


async def areplay(
    run_id: str | RecordedRun,
    *,
    target: ReplayTarget | None = None,
    model: ModelSetting = "recorded",
    tools: str = "recorded",
    context: ContextSetting = "original",
    temperature: float | None = None,
    system: str | None = None,
    input: Any = None,
    store: Any = None,
    strict: bool = False,
    tags: tuple[str, ...] = ("replay",),
) -> ReplayResult:
    """Replay a recorded run. See the module docstring for the three modes.

    ``temperature`` and ``system`` change one thing about the recorded
    requests and leave the rest of the transcript alone; both apply to prompt
    replay, where the recording *is* the prompt.
    """
    recorded = run_id if isinstance(run_id, RecordedRun) else RecordedRun.load(run_id, store=store)
    model_mode, replacement = _resolve_model(model)
    plan = _Plan(
        recorded=recorded,
        model_mode=model_mode,
        replacement=replacement,
        tool_mode=ComponentMode(tools),
        context_mode=_resolve_context(context),
        strict=strict,
        tags=tuple(tags),
        input=input,
        started=time.perf_counter(),
    )
    if target is not None:
        return await _re_execute(plan, target)
    if plan.replacement is not None:
        return await _replay_prompts(plan, plan.replacement, temperature=temperature, system=system)
    if model_mode is ComponentMode.LIVE or plan.tool_mode is ComponentMode.LIVE:
        raise ReplayError(
            "live replay needs target=<agent, graph or callable>; "
            "without one only a recorded reconstruction is possible"
        )
    return _reconstruct(plan)


def replay(
    run_id: str | RecordedRun,
    *,
    target: ReplayTarget | None = None,
    model: ModelSetting = "recorded",
    tools: str = "recorded",
    context: ContextSetting = "original",
    temperature: float | None = None,
    system: str | None = None,
    input: Any = None,
    store: Any = None,
    strict: bool = False,
    tags: tuple[str, ...] = ("replay",),
) -> ReplayResult:
    """Synchronous facade over :func:`areplay`."""
    return run_sync(
        areplay(
            run_id,
            target=target,
            model=model,
            tools=tools,
            context=context,
            temperature=temperature,
            system=system,
            input=input,
            store=store,
            strict=strict,
            tags=tags,
        )
    )


# Modes ------------------------------------------------------------------------
def _reconstruct(plan: _Plan) -> ReplayResult:
    """Re-emit the recording into a fresh run: deterministic replay."""
    recorded = plan.recorded
    with start_run(
        f"replay:{recorded.manifest.name}",
        tags=("replay", "reconstruct"),
        metadata={"replay_of": recorded.id, "mode": ReplayMode.RECONSTRUCT.value},
    ) as run:
        run.manifest.input = recorded.input
        substitutions = 0
        for event in recorded.events:
            if event.type in _LIFECYCLE:
                continue
            run.emit(
                event.type,
                dict(event.payload) | {"replayed_from": {"run_id": recorded.id, "seq": event.seq}},
            )
            if event.type in (EventType.MODEL_RESPONSE, EventType.TOOL_RETURNED):
                substitutions += 1
                run.emit(
                    EventType.REPLAY_SUBSTITUTED,
                    {
                        "kind": "model" if event.type is EventType.MODEL_RESPONSE else "tool",
                        "source_run_id": recorded.id,
                        "source_seq": event.seq,
                    },
                )
        run.manifest.usage = recorded.manifest.usage.model_copy(deep=True)
        run.manifest.cost = recorded.manifest.cost.model_copy(deep=True)
        run.manifest.dependencies = list(recorded.manifest.dependencies)
        run.manifest.output = recorded.output
    return ReplayResult(
        **plan.base(ReplayMode.RECONSTRUCT, run.id),
        output=recorded.output,
        substitutions=substitutions,
        usage=_usage_of(recorded.manifest),
        cost=recorded.manifest.cost.total,
    )


async def _replay_prompts(
    plan: _Plan,
    replacement: Model,
    *,
    temperature: float | None = None,
    system: str | None = None,
) -> ReplayResult:
    """Re-issue every recorded model request against a replacement model."""
    recorded = plan.recorded
    async with start_run(
        f"replay:{recorded.manifest.name}",
        tags=("replay", "prompt"),
        metadata={
            "replay_of": recorded.id,
            "mode": ReplayMode.PROMPT.value,
            "model": f"{replacement.provider}:{replacement.name}",
        },
    ) as run:
        run.manifest.input = recorded.input
        entries = await replay_prompts(
            recorded,
            replacement,
            options={"temperature": temperature} if temperature is not None else None,
            system=system,
        )
        run.manifest.output = entries[-1].replay_text if entries else None
    usage, cost = prompt_totals(entries)
    return ReplayResult(
        **plan.base(ReplayMode.PROMPT, run.id),
        output=entries[-1].replay_text if entries else None,
        prompts=entries,
        usage=usage,
        cost=cost.total,
    )


async def _re_execute(plan: _Plan, target: ReplayTarget) -> ReplayResult:
    """Really run ``target`` with recorded components substituted."""
    recorded = plan.recorded
    hooks = ReplayHooks(recorded, model=plan.model_mode, tools=plan.tool_mode, strict=plan.strict)
    payload = plan.input if plan.input is not None else recorded.input
    suppress_context = False
    if plan.context_mode == "original" and recorded.model_calls:
        first = recorded.model_calls[0]
        if first.messages:
            payload = first.messages
            suppress_context = getattr(target, "context", None) is not None
    run = start_run(
        f"replay:{recorded.manifest.name}",
        tags=plan.tags,
        metadata={
            "replay_of": recorded.id,
            "mode": ReplayMode.EXECUTE.value,
            "model_mode": plan.model_mode.value,
            "tool_mode": plan.tool_mode.value,
            "context_mode": plan.context_mode,
        },
        hooks=hooks,
    )
    error: str | None = None
    output: Any = None
    with _maybe_disable_context(target, suppress_context), swapped_model(target, plan.replacement):
        try:
            async with run:
                run.manifest.input = payload
                output = _output_of(await _invoke(target, payload))
                run.manifest.output = output
        except ReplayError:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    return ReplayResult(
        **plan.base(ReplayMode.EXECUTE, run.id),
        output=output,
        substitutions=len(hooks.substitutions),
        mismatches=list(hooks.mismatches),
        usage=_usage_of(run.manifest),
        cost=run.manifest.cost.total,
        error=error,
    )


def _usage_of(manifest: Any) -> Usage:
    totals = manifest.usage
    return Usage(
        input_tokens=totals.input_tokens,
        output_tokens=totals.output_tokens,
        cache_read_tokens=totals.cache_read_tokens,
        cache_write_tokens=totals.cache_write_tokens,
        reasoning_tokens=totals.reasoning_tokens,
    )


@contextlib.contextmanager
def _maybe_disable_context(target: Any, active: bool) -> Iterator[None]:
    """Suppress context re-assembly when the recorded prompt is replayed verbatim."""
    if not active:
        yield
        return
    original = target.context
    target.context = None
    try:
        yield
    finally:
        target.context = original
