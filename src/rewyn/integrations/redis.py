"""Redis-backed memory and checkpoints (spec §51).

Redis is where a fleet of workers keeps state they all need to see. A file
store is fine on a laptop and useless behind a load balancer: the second
replica cannot read the first one's memory.

Both classes speak the existing protocols, so they drop into
``Memory(store=...)`` and ``Checkpointer(store)`` with nothing else changing.
The client is injected or imported lazily, so the core install never grows a
Redis dependency::

    pip install "rewyn[redis]"

Keys are namespaced (``rewyn:memory:<namespace>:<id>``), which is what
makes one Redis safe to share between tenants and between projects.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from rewyn.core.types import MissingDependencyError
from rewyn.memory.memory import MemoryHit, MemoryItem, MemoryKind, lexical_score
from rewyn.runtime.checkpoint import Checkpoint
from rewyn.security.redaction import default_redactor

DEFAULT_URL = "redis://localhost:6379/0"


def _client(url: str, client: Any = None) -> Any:
    if client is not None:
        return client
    try:
        from redis.asyncio import Redis
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise MissingDependencyError("redis", "redis") from exc
    return Redis.from_url(url, decode_responses=True)


class RedisMemoryStore:
    """Memory shared by every worker that can reach the same Redis."""

    name = "redis"

    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        client: Any = None,
        namespace: str = "default",
        prefix: str = "rewyn:memory",
        ttl_seconds: int | None = None,
    ) -> None:
        self.client = _client(url, client)
        self.namespace = namespace
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds

    def for_namespace(self, namespace: str) -> RedisMemoryStore:
        """Another tenant is another key prefix, not a filtered view."""
        if namespace == self.namespace:
            return self
        return RedisMemoryStore(
            client=self.client,
            namespace=namespace,
            prefix=self.prefix,
            ttl_seconds=self.ttl_seconds,
        )

    # Keys ---------------------------------------------------------------------
    @property
    def _index(self) -> str:
        return f"{self.prefix}:{self.namespace}:ids"

    def _key(self, item_id: str) -> str:
        return f"{self.prefix}:{self.namespace}:{item_id}"

    # MemoryStore --------------------------------------------------------------
    async def add(self, item: MemoryItem) -> MemoryItem:
        stamped = item.model_copy(update={"namespace": self.namespace})
        payload = json.dumps(default_redactor().redact(stamped.model_dump(mode="json")))
        await self.client.set(self._key(item.id), payload, ex=self.ttl_seconds)
        await self.client.sadd(self._index, item.id)
        return stamped

    async def get(self, item_id: str) -> MemoryItem | None:
        raw = await self.client.get(self._key(item_id))
        return MemoryItem.model_validate_json(raw) if raw else None

    async def delete(self, item_id: str) -> bool:
        removed = await self.client.delete(self._key(item_id))
        await self.client.srem(self._index, item_id)
        return bool(removed)

    async def list_items(
        self, *, kinds: Sequence[MemoryKind] | None = None, limit: int | None = None
    ) -> list[MemoryItem]:
        ids = sorted(await self.client.smembers(self._index))
        found: list[MemoryItem] = []
        for item_id in ids:
            item = await self.get(item_id)
            if item is None:
                # Expired by its TTL; drop the dangling index entry.
                await self.client.srem(self._index, item_id)
                continue
            if kinds is None or item.kind in kinds:
                found.append(item)
        found.sort(key=lambda i: i.updated_at, reverse=True)
        return found[:limit] if limit else found

    async def search(
        self, query: str, *, kinds: Sequence[MemoryKind] | None = None, limit: int = 5
    ) -> list[MemoryHit]:
        hits = [
            MemoryHit(item=item, score=lexical_score(query, item))
            for item in await self.list_items(kinds=kinds)
        ]
        hits = [h for h in hits if h.score > 0]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    async def clear(self, *, kinds: Sequence[MemoryKind] | None = None) -> int:
        doomed = await self.list_items(kinds=kinds)
        for item in doomed:
            await self.delete(item.id)
        return len(doomed)


class RedisCheckpointStore:
    """Checkpoints any worker can resume from."""

    name = "redis"

    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        client: Any = None,
        prefix: str = "rewyn:checkpoint",
        ttl_seconds: int | None = None,
    ) -> None:
        self.client = _client(url, client)
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds

    def _key(self, checkpoint_id: str) -> str:
        return f"{self.prefix}:{checkpoint_id}"

    def _run_key(self, run_id: str) -> str:
        return f"{self.prefix}:run:{run_id}"

    async def save(self, checkpoint: Checkpoint) -> None:
        payload = json.dumps(default_redactor().redact(checkpoint.model_dump(mode="json")))
        await self.client.set(self._key(checkpoint.id), payload, ex=self.ttl_seconds)
        await self.client.sadd(self._run_key(checkpoint.run_id), checkpoint.id)

    async def load(self, checkpoint_id: str) -> Checkpoint | None:
        raw = await self.client.get(self._key(checkpoint_id))
        return Checkpoint.model_validate_json(raw) if raw else None

    async def list_for_run(self, run_id: str) -> list[Checkpoint]:
        ids = sorted(await self.client.smembers(self._run_key(run_id)))
        found = [c for c in [await self.load(i) for i in ids] if c is not None]
        return sorted(found, key=lambda c: c.sequence)
