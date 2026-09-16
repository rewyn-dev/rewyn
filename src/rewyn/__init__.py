"""Rewyn: universal AI engineering SDK.

The top-level package exposes subpackages and the most common entry points
lazily so that ``from rewyn import models`` never imports the agent, RAG
or cloud stacks.
"""

from __future__ import annotations

import importlib
from importlib import metadata
from typing import Any

try:
    __version__ = metadata.version("rewyn")
except metadata.PackageNotFoundError:  # pragma: no cover - source checkout
    __version__ = "0.0.0"

_SUBPACKAGES: frozenset[str] = frozenset(
    {
        "agents",
        "cli",
        "context",
        "core",
        "evaluation",
        "graphs",
        "guardrails",
        "human",
        "integrations",
        "mcp",
        "memory",
        "models",
        "rag",
        "replay",
        "runtime",
        "security",
        "skills",
        "storage",
        "testing",
        "tools",
    }
)

# Attribute name -> (module, attribute) for the spec's headline entry points.
# Entries are added as the corresponding subsystem is implemented. Names that
# collide with a subpackage stay out of this table: ``rewyn.replay`` is the
# module, and the function is ``from rewyn.replay import replay``.
_ENTRY_POINTS: dict[str, tuple[str, str]] = {
    "Agent": ("rewyn.agents.agent", "Agent"),
    "BehaviorManifest": ("rewyn.core.manifest", "BehaviorManifest"),
    "Context": ("rewyn.context.manager", "Context"),
    "Dataset": ("rewyn.evaluation.dataset", "Dataset"),
    "Graph": ("rewyn.graphs.graph", "Graph"),
    "Memory": ("rewyn.memory.memory", "Memory"),
    "Run": ("rewyn.core.run", "Run"),
    "RunResult": ("rewyn.agents.agent", "RunResult"),
    "current_run": ("rewyn.core.run", "current_run"),
    "detect_drift": ("rewyn.core.manifest", "detect_drift"),
    "diff": ("rewyn.replay.diff", "diff"),
    "evaluate": ("rewyn.evaluation.evaluator", "evaluate"),
    "evaluator": ("rewyn.evaluation.evaluator", "evaluator"),
    "run_regression": ("rewyn.evaluation.regression", "run_regression"),
    "start_run": ("rewyn.core.run", "start_run"),
    "tool": ("rewyn.tools.tool", "tool"),
}

__all__ = [
    "Agent",
    "BehaviorManifest",
    "Context",
    "Dataset",
    "Graph",
    "Memory",
    "Run",
    "RunResult",
    "__version__",
    "agents",
    "cli",
    "context",
    "core",
    "current_run",
    "detect_drift",
    "diff",
    "evaluate",
    "evaluation",
    "evaluator",
    "graphs",
    "guardrails",
    "human",
    "integrations",
    "mcp",
    "memory",
    "models",
    "rag",
    "replay",
    "run_regression",
    "runtime",
    "security",
    "skills",
    "start_run",
    "storage",
    "testing",
    "tool",
    "tools",
]


def __getattr__(name: str) -> Any:
    if name == "sandbox":
        return importlib.import_module("rewyn.runtime.sandbox")
    if name in _SUBPACKAGES:
        return importlib.import_module(f"rewyn.{name}")
    if name in _ENTRY_POINTS:
        module_name, attr = _ENTRY_POINTS[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module 'rewyn' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _SUBPACKAGES | set(_ENTRY_POINTS))
