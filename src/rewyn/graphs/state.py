"""Graph state and per-node execution context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rewyn.core.run import Run
from rewyn.core.state import State

if TYPE_CHECKING:  # pragma: no cover
    from rewyn.graphs.node import Node

INPUT_KEY = "input"
OUTPUT_KEY = "output"
PATH_KEY = "__path__"


@dataclass(slots=True)
class NodeContext:
    """What a node function may receive instead of the bare state."""

    run: Run
    state: State
    node: Node
    step: int
    attempt: int = 1
    approved: bool | None = None
    inputs: dict[str, Any] = field(default_factory=dict)


def apply_result(state: State, node_name: str, result: Any) -> dict[str, Any]:
    """Merge a node's return value into state. Dicts merge; other values are stored by name."""
    if result is None:
        return {}
    updates = dict(result) if isinstance(result, dict) else {node_name: result}
    fallback = None if isinstance(result, dict) else result
    updates.setdefault(OUTPUT_KEY, updates.get(node_name, fallback))
    if updates.get(OUTPUT_KEY) is None:
        updates.pop(OUTPUT_KEY, None)
    state.update(updates)
    return updates
