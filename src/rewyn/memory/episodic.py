"""Episodic memory: what happened in past runs."""

from __future__ import annotations

from typing import Any

from rewyn.memory.memory import Memory, MemoryItem, MemoryKind


async def record_episode(
    memory: Memory,
    *,
    task: str,
    outcome: str,
    success: bool,
    importance: float = 0.6,
    **details: Any,
) -> MemoryItem:
    """Store a compact record of an episode (task → outcome)."""
    content = f"Task: {task}\nOutcome: {outcome}\nResult: {'success' if success else 'failure'}"
    return await memory.remember(
        content,
        kind=MemoryKind.EPISODIC,
        importance=importance,
        tags=["episode", "success" if success else "failure"],
        task=task,
        success=success,
        **details,
    )


async def similar_episodes(memory: Memory, task: str, *, limit: int = 3) -> list[MemoryItem]:
    hits = await memory.recall(task, kinds=[MemoryKind.EPISODIC], limit=limit)
    return [h.item for h in hits]
