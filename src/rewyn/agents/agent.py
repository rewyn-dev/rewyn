"""The Agent primitive (spec §15, §18, §40, §49).

::

    from rewyn import Agent

    agent = Agent(model="anthropic:claude-opus-5", tools=[search])
    result = agent.run("Research the latest electric vehicle market")

Every run records ``AGENT_LOOP_STARTED``, one ``AGENT_LOOP_ITERATION`` per
iteration and ``AGENT_LOOP_FINISHED``; model and tool calls record their own
events inside the agent span.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.agents.handoff import handoff_tool, reset_loop_context, set_loop_context
from rewyn.agents.lifecycle import HookRunner
from rewyn.agents.loop import (
    Loop,
    LoopContext,
    LoopStrategy,
    StopCondition,
    StopReason,
    check_budget,
    get_strategy,
)
from rewyn.agents.subagent import subagent_tool
from rewyn.context.manager import Context
from rewyn.context.source import ContextItem
from rewyn.core.event import EventType
from rewyn.core.run import DependencyRef, Run, RunStatus, current_run, start_run
from rewyn.core.schema import fingerprint, schema_for_type
from rewyn.core.span import SpanKind
from rewyn.core.state import State
from rewyn.core.sync import run_sync
from rewyn.core.types import JSONObject
from rewyn.guardrails.policy import Guardrail, GuardrailPolicy, GuardrailViolationError
from rewyn.human.approval import ApprovalHandler, tool_approver
from rewyn.mcp.client import MCPClient, MCPServerConfig, connect
from rewyn.memory.memory import Memory, MemoryKind
from rewyn.models.base import (
    Message,
    MessageLike,
    Model,
    ModelResponse,
    StructuredOutputError,
    ToolCallPart,
    ToolResultPart,
    Usage,
    coerce_messages,
)
from rewyn.models.registry import resolve_model
from rewyn.runtime.checkpoint import Checkpointer
from rewyn.runtime.streaming import EventStream, StreamItem
from rewyn.skills.lifecycle import DisclosureMode, SkillManager
from rewyn.skills.skill import Skill
from rewyn.tools.execution import ToolExecutor
from rewyn.tools.permissions import PermissionPolicy
from rewyn.tools.registry import ToolRegistry
from rewyn.tools.tool import Tool


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    run_id: str
    agent: str
    output: str = ""
    structured: Any = None
    stop_reason: StopReason = StopReason.COMPLETED
    iterations: int = 0
    tool_calls: int = 0
    usage: Usage = Field(default_factory=Usage)
    cost: float = 0.0
    messages: list[Message] = Field(default_factory=list)
    state: JSONObject = Field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.stop_reason is StopReason.COMPLETED

    def __str__(self) -> str:
        return self.output


class Agent:
    def __init__(
        self,
        model: str | Model,
        *,
        name: str = "agent",
        instructions: str | None = None,
        tools: Sequence[Tool | Callable[..., Any]] = (),
        loop: Loop | str = "react",
        max_iterations: int | None = None,
        max_tokens: int | None = None,
        max_cost: float | None = None,
        max_time: float | None = None,
        output_schema: Any = None,
        permission_policy: PermissionPolicy | None = None,
        stop_when: StopCondition | None = None,
        version: str = "1",
        model_options: Mapping[str, Any] | None = None,
        tool_executor: ToolExecutor | None = None,
        raise_on_tool_error: bool = False,
        context: Context | None = None,
        memory: Memory | None = None,
        skills: Sequence[Skill | str | Path] = (),
        mcp: Sequence[MCPClient | MCPServerConfig | str] = (),
        skill_mode: DisclosureMode = "progressive",
        memory_autosave: bool = True,
        subagents: Sequence[Agent] = (),
        handoffs: Sequence[Agent] = (),
        guardrails: Sequence[Guardrail] = (),
        approval_handler: ApprovalHandler | None = None,
        hooks: Sequence[Any] = (),
        checkpointer: Checkpointer | None = None,
        stream_model: bool = False,
    ) -> None:
        self.model = resolve_model(model)
        self.name = name
        self.instructions = instructions
        self.version = version
        self.output_schema = output_schema
        self.model_options: dict[str, Any] = dict(model_options or {})
        self.stop_when = stop_when
        self.registry = ToolRegistry(tools)
        self.approval_handler = approval_handler
        self.executor = tool_executor or ToolExecutor(
            self.registry,
            policy=permission_policy,
            raise_on_error=raise_on_tool_error,
            approver=tool_approver(approval_handler) if approval_handler else None,
        )
        self.subagents = list(subagents)
        for sub in self.subagents:
            self.registry.register(subagent_tool(sub, parent=name), replace=True)
        self.handoffs = list(handoffs)
        for target in self.handoffs:
            self.registry.register(handoff_tool(self, target), replace=True)
        self.guardrails = GuardrailPolicy(guardrails, on_block="return")
        self.hooks = HookRunner(hooks)
        self.checkpointer = checkpointer
        self.stream_model = stream_model
        base = Loop(strategy=loop) if isinstance(loop, str) else loop
        overrides = {
            k: v
            for k, v in {
                "max_iterations": max_iterations,
                "max_tokens": max_tokens,
                "max_cost": max_cost,
                "max_time_seconds": max_time,
            }.items()
            if v is not None
        }
        self.loop = base.model_copy(update=overrides)
        self.strategy: LoopStrategy = get_strategy(self.loop.strategy)
        self.memory = memory
        self.memory_autosave = memory_autosave
        self.skills = SkillManager(skills, mode=skill_mode)
        for tool in self.skills.tools():
            self.registry.register(tool, replace=True)
        self.mcp_clients: list[MCPClient] = [
            c if isinstance(c, MCPClient) else connect(c) for c in mcp
        ]
        self._opened_mcp: list[MCPClient] = []
        self.context = context
        if self.context is None and (memory is not None or len(self.skills.registry)):
            self.context = Context(name=f"{name}-context")
        if self.context is not None and memory is not None:
            self.context.add_source(memory.as_context_source())

    # Lifecycle -------------------------------------------------------------------
    async def aclose(self) -> None:
        """Close MCP connections this agent opened."""
        for client in reversed(self._opened_mcp):
            await client.close()
        self._opened_mcp.clear()

    def close(self) -> None:
        run_sync(self.aclose())

    async def __aenter__(self) -> Agent:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _attach_mcp(self) -> None:
        for client in self.mcp_clients:
            if not client.opened:
                await client.open()
                self._opened_mcp.append(client)
            for tool in client.tools():
                self.registry.register(tool, replace=True)

    # Identity --------------------------------------------------------------------
    def fingerprint(self) -> str:
        return fingerprint(self.config())

    def config(self) -> JSONObject:
        return {
            "name": self.name,
            "version": self.version,
            "model": f"{self.model.provider}:{self.model.name}",
            "instructions": self.instructions,
            "tools": {t.name: t.fingerprint() for t in self.registry},
            "loop": self.loop.model_dump(),
            "output_schema": schema_for_type(self.output_schema)
            if self.output_schema is not None and not isinstance(self.output_schema, dict)
            else self.output_schema,
            "model_options": self.model_options,
            "context": self.context.fingerprint() if self.context else None,
            "memory": self.memory.name if self.memory else None,
            "skills": {s.name: s.fingerprint() for s in self.skills.registry},
            "mcp": {c.config.name: c.config.fingerprint() for c in self.mcp_clients},
            "subagents": {a.name: a.fingerprint() for a in self.subagents},
            "handoffs": [a.name for a in self.handoffs],
            "guardrails": [g.name for g in self.guardrails.guardrails],
        }

    @property
    def dependency(self) -> DependencyRef:
        return DependencyRef(
            kind="agent", name=self.name, version=self.version, fingerprint=self.fingerprint()
        )

    # Execution -------------------------------------------------------------------
    def run(
        self,
        input: MessageLike | Sequence[MessageLike],
        *,
        state: Mapping[str, Any] | State | None = None,
        tags: Sequence[str] = (),
        resume_from: str | None = None,
    ) -> RunResult:
        return run_sync(self.arun(input, state=state, tags=tags, resume_from=resume_from))

    async def arun(
        self,
        input: MessageLike | Sequence[MessageLike],
        *,
        state: Mapping[str, Any] | State | None = None,
        tags: Sequence[str] = (),
        resume_from: str | None = None,
    ) -> RunResult:
        active = current_run()
        if active is not None:
            return await self._execute(active, input, state, resume_from)
        async with start_run(self.name, tags=tags, input=input) as run:
            result = await self._execute(run, input, state, resume_from)
            run.manifest.output = result.output
            return result

    async def astream(
        self,
        input: MessageLike | Sequence[MessageLike],
        *,
        state: Mapping[str, Any] | State | None = None,
        tags: Sequence[str] = (),
    ) -> AsyncIterator[StreamItem | RunResult]:
        """Yield run events and model deltas live; the final item is the ``RunResult``."""
        active = current_run()
        run = active if active is not None else start_run(self.name, tags=tags, input=input)
        original = self.stream_model
        self.stream_model = True
        try:
            with EventStream(run, stop_on={EventType.AGENT_LOOP_FINISHED}) as stream:
                if active is None:
                    run.start(input=input)
                task = asyncio.ensure_future(self._execute(run, input, state, None))
                async for item in stream:
                    yield item
                result = await task
            yield result
        except BaseException as exc:
            if active is None:
                run.finish(status=RunStatus.FAILED, error=exc)
            raise
        else:
            if active is None:
                run.manifest.output = result.output
                run.finish()
        finally:
            self.stream_model = original

    async def _execute(
        self,
        run: Run,
        input: MessageLike | Sequence[MessageLike],
        state_in: Any,
        resume_from: str | None = None,
    ) -> RunResult:
        run.add_dependency(self.dependency)
        run.add_dependency(self.model.dependency)
        # Every tool and guardrail the agent *offers* is a dependency of the run,
        # not only the ones a given transcript happened to invoke (spec §34).
        for available in self.registry:
            run.add_dependency(available.dependency)
        for guardrail in self.guardrails.guardrails:
            run.add_dependency(
                DependencyRef(
                    kind="guardrail", name=guardrail.name, metadata={"stage": guardrail.stage}
                )
            )
        if self.memory is not None:
            run.add_dependency(self.memory.dependency)
        self.skills.load(run)
        await self._attach_mcp()
        state = state_in if isinstance(state_in, State) else State(state_in)
        checkpoint = None
        if resume_from is not None:
            checkpointer = self.checkpointer or Checkpointer()
            checkpoint = await checkpointer.restore(resume_from)
            state.restore(checkpoint.state)
            messages = list(checkpoint.messages)
        else:
            messages = await self.prepare_messages(input)
        ctx = LoopContext(run, messages, state)
        if checkpoint is not None:
            ctx.iteration = int(checkpoint.cursor.get("iteration", 0))
            ctx.tool_calls = int(checkpoint.cursor.get("tool_calls", 0))
        blocked = await self._guard_input(ctx)
        loop_token = set_loop_context(ctx)
        with run.span(f"agent:{self.name}", SpanKind.AGENT, version=self.version):
            run.emit(
                EventType.AGENT_LOOP_STARTED,
                {
                    "agent": self.name,
                    "version": self.version,
                    "fingerprint": self.fingerprint(),
                    "strategy": self.loop.strategy,
                    "budget": self.loop.model_dump(exclude={"strategy", "strategy_options"}),
                    "tools": self.registry.names(),
                    "input": [m.model_dump(mode="json") for m in messages],
                },
            )
            stop_reason = StopReason.COMPLETED
            error: str | None = None
            try:
                await self.hooks.dispatch("on_start", self, ctx)
                if blocked is not None:
                    stop_reason = StopReason.GUARDRAIL
                    ctx.output = blocked
                else:
                    stop_reason = await self._iterate(ctx)
                    if stop_reason is StopReason.COMPLETED:
                        stop_reason = await self._guard_output(ctx)
            except Exception as exc:
                stop_reason = StopReason.ERROR
                error = f"{type(exc).__name__}: {exc}"
                run.emit(
                    EventType.AGENT_LOOP_FINISHED,
                    self._finished_payload(ctx, stop_reason, error),
                )
                await self.hooks.dispatch("on_error", self, ctx, exc)
                raise
            finally:
                reset_loop_context(loop_token)
            run.emit(EventType.AGENT_LOOP_FINISHED, self._finished_payload(ctx, stop_reason, None))
            await self._autosave_memory(ctx, stop_reason)
        result = RunResult(
            run_id=run.id,
            agent=self.name,
            output=ctx.output,
            structured=ctx.structured,
            stop_reason=stop_reason,
            iterations=ctx.iteration,
            tool_calls=ctx.tool_calls,
            usage=ctx.usage,
            cost=ctx.cost,
            messages=list(ctx.messages),
            state=state.to_dict(),
            error=error,
        )
        await self.hooks.dispatch("on_finish", self, ctx, result)
        return result

    async def _guard_input(self, ctx: LoopContext) -> str | None:
        """Run input guardrails over the user text. Returns a block message or None."""
        if not self.guardrails.for_stage("input"):
            return None
        for index, message in enumerate(ctx.messages):
            if message.role.value != "user":
                continue
            outcome = await self.guardrails.check_input(message.text, agent=self.name)
            if outcome.blocked:
                decision = outcome.triggered[-1]
                return f"Input blocked by guardrail {decision.guardrail!r}: {decision.reason}"
            if outcome.text != message.text:
                ctx.messages[index] = Message.user(outcome.text)
        return None

    async def _guard_output(self, ctx: LoopContext) -> StopReason:
        if not self.guardrails.for_stage("output") or not ctx.output:
            return StopReason.COMPLETED
        try:
            outcome = await self.guardrails.check_output(ctx.output, agent=self.name)
        except GuardrailViolationError as exc:  # pragma: no cover - on_block is "return"
            ctx.output = str(exc)
            return StopReason.GUARDRAIL
        if outcome.blocked:
            decision = outcome.triggered[-1]
            ctx.output = f"Output blocked by guardrail {decision.guardrail!r}: {decision.reason}"
            return StopReason.GUARDRAIL
        ctx.output = outcome.text
        return StopReason.COMPLETED

    async def _iterate(self, ctx: LoopContext) -> StopReason:
        while True:
            exhausted = check_budget(self.loop, ctx)
            if exhausted is not None:
                self._salvage_output(ctx)
                return exhausted
            if self.stop_when is not None and await _evaluate(self.stop_when, ctx):
                self._salvage_output(ctx)
                if getattr(self.stop_when, "is_evaluator", False):
                    return StopReason.EVALUATOR
                return StopReason.CUSTOM
            ctx.iteration += 1
            with ctx.run.span(f"iteration:{ctx.iteration}", SpanKind.ITERATION):
                done = await self.strategy.step(self, ctx)
                if "handoff" in ctx.scratch:
                    done = True
                ctx.run.emit(
                    EventType.AGENT_LOOP_ITERATION,
                    {
                        "agent": self.name,
                        "iteration": ctx.iteration,
                        "done": done,
                        "tool_calls": ctx.tool_calls,
                        "usage": ctx.usage.model_dump(),
                        "cost": ctx.cost,
                    },
                )
                await self.hooks.dispatch("on_iteration", self, ctx, done)
                if self.checkpointer is not None:
                    await self.checkpointer.save(
                        state=ctx.state,
                        messages=ctx.messages,
                        cursor={"iteration": ctx.iteration, "tool_calls": ctx.tool_calls},
                        label=f"iteration {ctx.iteration}",
                        owner=f"agent:{self.name}",
                        run=ctx.run,
                    )
            if "handoff" in ctx.scratch:
                return StopReason.HANDOFF
            if done:
                await self._ensure_structured(ctx)
                return StopReason.COMPLETED

    def _salvage_output(self, ctx: LoopContext) -> None:
        if not ctx.output and ctx.last_response is not None:
            ctx.output = ctx.last_response.text

    async def _ensure_structured(self, ctx: LoopContext) -> None:
        if self.output_schema is None:
            return
        last = ctx.last_response
        if last is not None and last.validation is not None and last.validation.valid:
            ctx.structured = last.structured
            return
        # One repair attempt: ask the model to restate the answer in the required format.
        ctx.messages.append(
            Message.user("Restate your final answer as JSON matching the required schema.")
        )
        response = await self.model.agenerate(
            ctx.messages, output_schema=self.output_schema, strict_output=True, **self.model_options
        )
        ctx.account(response)
        ctx.messages.append(response.message)
        ctx.output = response.text
        ctx.structured = response.structured

    def _finished_payload(
        self, ctx: LoopContext, stop: StopReason, error: str | None
    ) -> JSONObject:
        return {
            "agent": self.name,
            "stop_reason": stop.value,
            "iterations": ctx.iteration,
            "tool_calls": ctx.tool_calls,
            "usage": ctx.usage.model_dump(),
            "cost": ctx.cost,
            "output": ctx.output,
            "error": error,
        }

    async def _autosave_memory(self, ctx: LoopContext, stop_reason: StopReason) -> None:
        if self.memory is None or not self.memory_autosave:
            return
        user_text = next((m.text for m in ctx.messages if m.role.value == "user"), "")
        if MemoryKind.SHORT_TERM in self.memory.enabled and user_text:
            await self.memory.remember(
                f"user: {user_text}\nassistant: {ctx.output}",
                kind=MemoryKind.SHORT_TERM,
                importance=0.4,
                tags=["conversation", self.name],
            )
        if MemoryKind.EPISODIC in self.memory.enabled and user_text:
            from rewyn.memory.episodic import record_episode

            await record_episode(
                self.memory,
                task=user_text,
                outcome=ctx.output[:500],
                success=stop_reason is StopReason.COMPLETED,
                agent=self.name,
                run_id=ctx.run.id,
            )

    # Building blocks used by strategies ------------------------------------------
    async def prepare_messages(self, input: MessageLike | Sequence[MessageLike]) -> list[Message]:
        """Build the initial transcript; assembles the context engine when configured."""
        if self.context is None:
            return self.initial_messages(input)
        messages = coerce_messages(input)
        query = next((m.text for m in reversed(messages) if m.role.value == "user"), None)
        extra: list[ContextItem] = list(self.skills.context_items())
        from rewyn.context.source import ContextKind, TrustLevel

        has_instructions = any(
            i.kind is ContextKind.INSTRUCTIONS for i in self.context._static.items
        )
        if self.instructions and not has_instructions:
            self.context.add(
                ContextItem(
                    kind=ContextKind.INSTRUCTIONS,
                    content=self.instructions,
                    required=True,
                    trust_level=TrustLevel.SYSTEM,
                    task_importance=1.0,
                )
            )
        assembled = await self.context.assemble(query, extra=extra)
        system = assembled.to_messages()
        return [*system, *[m for m in messages if m.role.value != "system"]]

    def initial_messages(self, input: MessageLike | Sequence[MessageLike]) -> list[Message]:
        messages = coerce_messages(input)
        system = self.system_prompt()
        if system and not any(m.role.value == "system" for m in messages):
            messages.insert(0, Message.system(system))
        return messages

    def system_prompt(self) -> str | None:
        return self.instructions

    async def call_model(self, ctx: LoopContext) -> ModelResponse:
        options: dict[str, Any] = {
            "tools": self.registry.specs() or None,
            "output_schema": self.output_schema,
            "strict_output": False,
            **self.model_options,
        }
        try:
            if self.stream_model:
                response = await self._stream_model(ctx, options)
            else:
                response = await self.model.agenerate(ctx.messages, **options)
        except StructuredOutputError as exc:  # pragma: no cover - strict_output is False
            response = exc.response
        ctx.account(response)
        await self.hooks.dispatch("on_model_response", self, ctx, response)
        return response

    async def _stream_model(self, ctx: LoopContext, options: dict[str, Any]) -> ModelResponse:
        final: ModelResponse | None = None
        async for event in self.model.astream(ctx.messages, **options):
            if event.type == "response":
                final = event.response
            else:
                ctx.run.publish(event)
        if final is None:  # pragma: no cover - models always end with a response
            raise RuntimeError("model stream ended without a response")
        return final

    async def run_tools(
        self, ctx: LoopContext, calls: Sequence[ToolCallPart]
    ) -> list[ToolResultPart]:
        results = await self.executor.execute_all(calls)
        ctx.tool_calls += len(results)
        await self.hooks.dispatch("on_tool_results", self, ctx, results)
        return results


async def _evaluate(condition: StopCondition, ctx: LoopContext) -> bool:
    value = condition(ctx)
    if inspect.isawaitable(value):
        return bool(await value)
    return bool(value)
