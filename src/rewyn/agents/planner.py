"""Planning and reflection loop strategies (spec §15).

- ``plan_execute``: the model writes a numbered plan (``PLAN_CREATED``), then
  executes one step per iteration with tools, then produces the final answer.
- ``reflection``: ReAct until an answer, then a critic pass; if the critic
  asks for revision the feedback is fed back, up to ``max_reflections`` times.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from rewyn.agents.loop import LoopContext, ReActLoop, register_strategy
from rewyn.core.event import EventType
from rewyn.models.base import Message

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.agents.agent import Agent


class Plan(BaseModel):
    goal: str
    steps: list[str] = Field(min_length=1)


class Critique(BaseModel):
    approved: bool
    feedback: str = ""


class PlanExecuteLoop:
    name = "plan_execute"

    async def step(self, agent: Agent, ctx: LoopContext) -> bool:
        plan: Plan | None = ctx.scratch.get("plan")
        if plan is None:
            return await self._plan(agent, ctx)
        index: int = ctx.scratch.get("step_index", 0)
        if index < len(plan.steps):
            return await self._execute_step(agent, ctx, plan, index)
        return await self._finish(agent, ctx)

    async def _plan(self, agent: Agent, ctx: LoopContext) -> bool:
        prompt = Message.user(
            "Before acting, write a short plan: the goal and the concrete steps you will take "
            "(use the available tools where helpful). Respond as JSON with 'goal' and 'steps'."
        )
        response = await agent.model.agenerate(
            [*ctx.messages, prompt],
            output_schema=Plan,
            strict_output=False,
            **agent.model_options,
        )
        ctx.account(response)
        plan = (
            response.structured
            if isinstance(response.structured, Plan)
            else Plan(goal=ctx.messages[-1].text, steps=[response.text or "Solve the task."])
        )
        ctx.scratch["plan"] = plan
        ctx.scratch["step_index"] = 0
        ctx.messages.append(
            Message.assistant(
                "Plan:\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(plan.steps))
            )
        )
        ctx.run.emit(
            EventType.PLAN_CREATED,
            {"agent": agent.name, "goal": plan.goal, "steps": plan.steps},
        )
        return False

    async def _execute_step(self, agent: Agent, ctx: LoopContext, plan: Plan, index: int) -> bool:
        ctx.messages.append(
            Message.user(
                f"Execute step {index + 1} of {len(plan.steps)}: {plan.steps[index]}. "
                "Use tools if needed and report the result of this step."
            )
        )
        response = await agent.call_model(ctx)
        ctx.messages.append(response.message)
        if response.tool_calls:
            results = await agent.run_tools(ctx, response.tool_calls)
            ctx.messages.append(Message.tool(results))
            follow_up = await agent.call_model(ctx)
            ctx.messages.append(follow_up.message)
            if follow_up.tool_calls:
                more = await agent.run_tools(ctx, follow_up.tool_calls)
                ctx.messages.append(Message.tool(more))
        ctx.scratch["step_index"] = index + 1
        return False

    async def _finish(self, agent: Agent, ctx: LoopContext) -> bool:
        ctx.messages.append(
            Message.user("All plan steps are done. Give the final answer to the original task.")
        )
        response = await agent.call_model(ctx)
        ctx.messages.append(response.message)
        ctx.output = response.text
        ctx.structured = response.structured
        return True


class ReflectionLoop:
    name = "reflection"

    def __init__(self, max_reflections: int = 2) -> None:
        self.max_reflections = max_reflections
        self._react = ReActLoop()

    async def step(self, agent: Agent, ctx: LoopContext) -> bool:
        done = await self._react.step(agent, ctx)
        if not done:
            return False
        reflections: int = ctx.scratch.get("reflections", 0)
        if reflections >= self.max_reflections:
            return True
        critique_prompt = Message.user(
            "Critically review your previous answer against the original task. Respond as JSON "
            "with 'approved' (true if the answer is complete and correct) and 'feedback' "
            "(what to fix if not approved)."
        )
        response = await agent.model.agenerate(
            [*ctx.messages, critique_prompt],
            output_schema=Critique,
            strict_output=False,
            **agent.model_options,
        )
        ctx.account(response)
        critique = response.structured if isinstance(response.structured, Critique) else None
        ctx.scratch["reflections"] = reflections + 1
        if critique is None or critique.approved:
            return True
        ctx.messages.append(
            Message.user(f"Revise your answer using this feedback: {critique.feedback}")
        )
        ctx.output = ""
        return False


register_strategy("plan_execute", PlanExecuteLoop)
register_strategy("reflection", ReflectionLoop)
