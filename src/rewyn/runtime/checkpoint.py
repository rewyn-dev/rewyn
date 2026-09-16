"""Checkpoints (spec §21): resumable state for long-running agents and graphs.

A checkpoint captures the versioned state, the message transcript and a
``cursor`` describing where execution should resume (iteration number,
graph frontier). Creating and restoring checkpoints are events.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.event import EventType
from rewyn.core.run import Run, aensure_run
from rewyn.core.schema import fingerprint
from rewyn.core.state import State, StateSnapshot
from rewyn.core.types import JSONObject, RewynError, new_id, utcnow
from rewyn.models.base import Message


class CheckpointNotFoundError(RewynError):
    pass


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("ckpt"))
    run_id: str
    sequence: int
    label: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    owner: str = "agent"
    state: StateSnapshot
    messages: list[Message] = Field(default_factory=list)
    cursor: JSONObject = Field(default_factory=dict)
    metadata: JSONObject = Field(default_factory=dict)

    def fingerprint(self) -> str:
        return fingerprint(
            {
                "state": self.state.fingerprint,
                "messages": [m.model_dump(mode="json") for m in self.messages],
                "cursor": self.cursor,
                "owner": self.owner,
            }
        )


class CheckpointStore(Protocol):
    name: str

    async def save(self, checkpoint: Checkpoint) -> None: ...

    async def load(self, checkpoint_id: str) -> Checkpoint | None: ...

    async def list_for_run(self, run_id: str) -> list[Checkpoint]: ...


class InMemoryCheckpointStore:
    name = "in_memory"

    def __init__(self) -> None:
        self._items: dict[str, Checkpoint] = {}

    async def save(self, checkpoint: Checkpoint) -> None:
        self._items[checkpoint.id] = checkpoint

    async def load(self, checkpoint_id: str) -> Checkpoint | None:
        return self._items.get(checkpoint_id)

    async def list_for_run(self, run_id: str) -> list[Checkpoint]:
        items = [c for c in self._items.values() if c.run_id == run_id]
        return sorted(items, key=lambda c: c.sequence)


class Checkpointer:
    """Create and restore checkpoints on the active run."""

    def __init__(self, store: CheckpointStore | None = None) -> None:
        if store is None:
            from rewyn.runtime.persistence import FileCheckpointStore

            store = FileCheckpointStore()
        self.store = store
        self._sequence: dict[str, int] = {}

    async def save(
        self,
        *,
        state: State,
        messages: list[Message] | None = None,
        cursor: JSONObject | None = None,
        label: str | None = None,
        owner: str = "agent",
        run: Run | None = None,
        **metadata: Any,
    ) -> Checkpoint:
        async with aensure_run("checkpoint") as active:
            run = run or active
            sequence = self._sequence.get(run.id, 0) + 1
            self._sequence[run.id] = sequence
            checkpoint = Checkpoint(
                run_id=run.id,
                sequence=sequence,
                label=label,
                owner=owner,
                state=state.snapshot(label or f"checkpoint {sequence}"),
                messages=list(messages or []),
                cursor=dict(cursor or {}),
                metadata=metadata,
            )
            await self.store.save(checkpoint)
            run.emit(
                EventType.CHECKPOINT_CREATED,
                {
                    "checkpoint_id": checkpoint.id,
                    "sequence": sequence,
                    "label": label,
                    "owner": owner,
                    "cursor": checkpoint.cursor,
                    "state_version": checkpoint.state.version,
                    "state_fingerprint": checkpoint.state.fingerprint,
                    "messages": len(checkpoint.messages),
                    "fingerprint": checkpoint.fingerprint(),
                    "store": self.store.name,
                },
            )
            return checkpoint

    async def restore(self, checkpoint_id: str) -> Checkpoint:
        checkpoint = await self.store.load(checkpoint_id)
        if checkpoint is None:
            raise CheckpointNotFoundError(f"checkpoint {checkpoint_id!r} not found")
        async with aensure_run("checkpoint") as run:
            run.emit(
                EventType.CHECKPOINT_RESTORED,
                {
                    "checkpoint_id": checkpoint.id,
                    "source_run_id": checkpoint.run_id,
                    "sequence": checkpoint.sequence,
                    "cursor": checkpoint.cursor,
                    "state_fingerprint": checkpoint.state.fingerprint,
                    "fingerprint": checkpoint.fingerprint(),
                },
            )
        return checkpoint

    async def latest(self, run_id: str) -> Checkpoint | None:
        items = await self.store.list_for_run(run_id)
        return items[-1] if items else None
