"""Agents: loops, strategies, subagents and handoffs."""

from rewyn.agents import planner as _planner  # noqa: F401 - registers strategies
from rewyn.agents.agent import Agent, RunResult
from rewyn.agents.handoff import Handoff, handoff, handoff_tool
from rewyn.agents.lifecycle import AgentHooks, LoggingHooks
from rewyn.agents.loop import (
    Loop,
    LoopContext,
    LoopStrategy,
    ReActLoop,
    StopReason,
    evaluator_stop,
    get_strategy,
    known_strategies,
    register_strategy,
)
from rewyn.agents.planner import PlanExecuteLoop, ReflectionLoop
from rewyn.agents.subagent import run_subagent, subagent_tool

__all__ = [
    "Agent",
    "AgentHooks",
    "Handoff",
    "LoggingHooks",
    "Loop",
    "LoopContext",
    "LoopStrategy",
    "PlanExecuteLoop",
    "ReActLoop",
    "ReflectionLoop",
    "RunResult",
    "StopReason",
    "evaluator_stop",
    "get_strategy",
    "handoff",
    "handoff_tool",
    "known_strategies",
    "register_strategy",
    "run_subagent",
    "subagent_tool",
]
