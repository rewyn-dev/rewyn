"""Long-term memory: a persistent :class:`Memory` backed by the file provider."""

from __future__ import annotations

from pathlib import Path

from rewyn.memory.memory import Memory
from rewyn.memory.providers.file import FileStore


def long_term_memory(
    namespace: str = "default", *, path: Path | None = None, name: str = "long_term"
) -> Memory:
    """A ``Memory`` whose episodic/semantic/procedural kinds persist across processes."""
    return Memory(
        short_term=True,
        long_term=True,
        store=FileStore(path, namespace=namespace),
        namespace=namespace,
        name=name,
    )
