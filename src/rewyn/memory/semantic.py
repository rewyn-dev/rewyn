"""Semantic memory: durable facts, and procedural memory: how to do things."""

from __future__ import annotations

from collections.abc import Iterable

from rewyn.memory.memory import Memory, MemoryItem, MemoryKind


async def remember_fact(
    memory: Memory, fact: str, *, importance: float = 0.6, tags: Iterable[str] = ()
) -> MemoryItem:
    return await memory.remember(fact, kind=MemoryKind.SEMANTIC, importance=importance, tags=tags)


async def remember_procedure(
    memory: Memory, name: str, steps: Iterable[str], *, importance: float = 0.7
) -> MemoryItem:
    body = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(steps))
    return await memory.remember(
        f"Procedure: {name}\n{body}",
        kind=MemoryKind.PROCEDURAL,
        importance=importance,
        tags=["procedure", name],
    )


async def facts_about(memory: Memory, topic: str, *, limit: int = 5) -> list[MemoryItem]:
    hits = await memory.recall(topic, kinds=[MemoryKind.SEMANTIC], limit=limit)
    return [h.item for h in hits]
