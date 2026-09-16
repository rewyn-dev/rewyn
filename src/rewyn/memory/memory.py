"""Memory primitives (spec §10).

Five memory types share one item model and one store protocol so
applications can mix providers freely. ``Memory`` is the facade agents use:

::

    memory = Memory(short_term=True, long_term=True)
    await memory.remember("Acme prefers invoices on the 1st", kind="semantic")
    hits = await memory.recall("Acme invoicing")

Reads emit ``MEMORY_READ``; writes emit ``MEMORY_WRITE``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from rewyn.context.source import ContextItem, ContextKind, Provenance, TrustLevel
from rewyn.core.event import EventType
from rewyn.core.run import DependencyRef, aensure_run
from rewyn.core.span import SpanKind
from rewyn.core.sync import run_sync
from rewyn.core.types import JSONObject, new_id, utcnow


class MemoryKind(StrEnum):
    WORKING = "working"
    SHORT_TERM = "short_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class MemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("mem"))
    kind: MemoryKind
    content: str
    importance: float = 0.5
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    tags: list[str] = Field(default_factory=list)
    metadata: JSONObject = Field(default_factory=dict)
    run_id: str | None = None
    namespace: str = "default"
    """The tenant this item belongs to. Set by :class:`Memory`, enforced on read."""

    def to_context_item(self, relevance: float = 0.5) -> ContextItem:
        return ContextItem(
            kind=ContextKind.MEMORY,
            title=f"{self.kind.value} memory",
            content=self.content,
            relevance=relevance,
            recency=self.updated_at,
            user_importance=self.importance,
            trust_level=TrustLevel.INTERNAL,
            provenance=Provenance.for_content("memory", self.content, record=self.id),
            metadata={"memory_id": self.id, "kind": self.kind.value},
        )


class MemoryHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    item: MemoryItem
    score: float


@runtime_checkable
class MemoryStore(Protocol):
    """Storage backend for memory items. Implementations must be async."""

    name: str

    async def add(self, item: MemoryItem) -> MemoryItem: ...

    async def get(self, item_id: str) -> MemoryItem | None: ...

    async def delete(self, item_id: str) -> bool: ...

    async def list_items(
        self, *, kinds: Sequence[MemoryKind] | None = None, limit: int | None = None
    ) -> list[MemoryItem]: ...

    async def search(
        self, query: str, *, kinds: Sequence[MemoryKind] | None = None, limit: int = 5
    ) -> list[MemoryHit]: ...

    async def clear(self, *, kinds: Sequence[MemoryKind] | None = None) -> int: ...


@runtime_checkable
class ScopedMemoryStore(Protocol):
    """A store that can isolate a namespace natively, without filtering."""

    def for_namespace(self, namespace: str) -> MemoryStore: ...


class NamespacedStore:
    """Bind a store to one namespace.

    Namespaces are the tenant boundary, so this is a correctness wrapper, not
    a convenience: writes are stamped, and reads, deletes and clears refuse to
    cross the boundary. Stores that can scope themselves natively (a file per
    namespace, a key prefix) implement ``for_namespace`` instead and are used
    directly.
    """

    def __init__(self, store: MemoryStore, namespace: str) -> None:
        self.inner = store
        self.namespace = namespace
        self.name = f"{store.name}[{namespace}]"

    def _mine(self, item: MemoryItem | None) -> bool:
        return item is not None and item.namespace == self.namespace

    async def add(self, item: MemoryItem) -> MemoryItem:
        return await self.inner.add(item.model_copy(update={"namespace": self.namespace}))

    async def get(self, item_id: str) -> MemoryItem | None:
        found = await self.inner.get(item_id)
        return found if self._mine(found) else None

    async def delete(self, item_id: str) -> bool:
        if not self._mine(await self.inner.get(item_id)):
            return False
        return await self.inner.delete(item_id)

    async def list_items(
        self, *, kinds: Sequence[MemoryKind] | None = None, limit: int | None = None
    ) -> list[MemoryItem]:
        # Filter before applying the limit, or another tenant's items would
        # consume the budget and hide our own.
        found = [i for i in await self.inner.list_items(kinds=kinds) if self._mine(i)]
        return found[:limit] if limit else found

    async def search(
        self, query: str, *, kinds: Sequence[MemoryKind] | None = None, limit: int = 5
    ) -> list[MemoryHit]:
        hits = await self.inner.search(query, kinds=kinds, limit=limit * 8 or 8)
        return [h for h in hits if self._mine(h.item)][:limit]

    async def clear(self, *, kinds: Sequence[MemoryKind] | None = None) -> int:
        doomed = await self.list_items(kinds=kinds)
        for item in doomed:
            await self.inner.delete(item.id)
        return len(doomed)


def scoped(store: MemoryStore, namespace: str) -> MemoryStore:
    """Return ``store`` restricted to ``namespace``."""
    if isinstance(store, NamespacedStore):
        store = store.inner
    native = getattr(store, "for_namespace", None)
    if callable(native):
        return native(namespace)  # type: ignore[no-any-return]
    return NamespacedStore(store, namespace)


_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def lexical_score(query: str, item: MemoryItem, *, now: datetime | None = None) -> float:
    """Overlap-based relevance blended with importance and recency; in [0, 1]."""
    q = set(tokenize(query))
    if not q:
        return item.importance * 0.5
    words = set(tokenize(item.content))
    overlap = len(q & words) / len(q)
    age_days = max(0.0, ((now or utcnow()) - item.updated_at).total_seconds() / 86_400)
    recency = 1.0 / (1.0 + age_days / 30.0)
    return round(0.7 * overlap + 0.2 * item.importance + 0.1 * recency, 6)


class Memory:
    """Facade over per-kind stores with events and context integration."""

    def __init__(
        self,
        *,
        short_term: bool = True,
        long_term: bool = True,
        store: MemoryStore | None = None,
        stores: dict[MemoryKind, MemoryStore] | None = None,
        namespace: str = "default",
        name: str = "memory",
        version: str = "1",
    ) -> None:
        from rewyn.memory.providers.in_memory import InMemoryStore

        self.name = name
        self.version = version
        self.namespace = namespace
        self._stores: dict[MemoryKind, MemoryStore] = dict(stores or {})
        default = store or InMemoryStore()
        enabled: list[MemoryKind] = [MemoryKind.WORKING]
        if short_term:
            enabled.append(MemoryKind.SHORT_TERM)
        if long_term:
            enabled.extend([MemoryKind.EPISODIC, MemoryKind.SEMANTIC, MemoryKind.PROCEDURAL])
        for kind in enabled:
            self._stores.setdefault(kind, default)
        # Namespaces isolate tenants, so bind every store before use rather
        # than trusting each call site to pass the namespace through.
        self._stores = {k: scoped(v, namespace) for k, v in self._stores.items()}
        self.enabled = tuple(self._stores)

    # Identity ------------------------------------------------------------------
    @property
    def dependency(self) -> DependencyRef:
        providers = sorted({s.name for s in self._stores.values()})
        return DependencyRef(
            kind="memory",
            name=self.name,
            version=self.version,
            metadata={"providers": providers, "kinds": [k.value for k in self.enabled]},
        )

    def store_for(self, kind: MemoryKind) -> MemoryStore:
        try:
            return self._stores[kind]
        except KeyError:
            raise KeyError(f"memory kind {kind.value!r} is not enabled") from None

    # Writes --------------------------------------------------------------------
    async def remember(
        self,
        content: str,
        *,
        kind: MemoryKind | str = MemoryKind.SEMANTIC,
        importance: float = 0.5,
        tags: Iterable[str] = (),
        **metadata: Any,
    ) -> MemoryItem:
        kind = MemoryKind(kind)
        async with aensure_run("memory") as run:
            run.add_dependency(self.dependency)
            item = MemoryItem(
                kind=kind,
                content=content,
                importance=importance,
                tags=list(tags),
                metadata=metadata,
                run_id=run.id,
            )
            with run.span(f"memory:write:{kind.value}", SpanKind.MEMORY):
                stored = await self.store_for(kind).add(item)
                run.emit(
                    EventType.MEMORY_WRITE,
                    {
                        "memory": self.name,
                        "kind": kind.value,
                        "id": stored.id,
                        "content": stored.content,
                        "importance": stored.importance,
                        "tags": stored.tags,
                        "provider": self.store_for(kind).name,
                    },
                )
            return stored

    def remember_sync(self, content: str, **kwargs: Any) -> MemoryItem:
        return run_sync(self.remember(content, **kwargs))

    async def forget(self, item_id: str) -> bool:
        for store in self._distinct_stores():
            if await store.delete(item_id):
                return True
        return False

    async def clear(self, *, kinds: Sequence[MemoryKind] | None = None) -> int:
        total = 0
        for store in self._distinct_stores():
            total += await store.clear(kinds=kinds)
        return total

    # Reads ---------------------------------------------------------------------
    async def recall(
        self,
        query: str,
        *,
        kinds: Sequence[MemoryKind | str] | None = None,
        limit: int = 5,
    ) -> list[MemoryHit]:
        wanted = [MemoryKind(k) for k in kinds] if kinds else list(self.enabled)
        async with aensure_run("memory") as run:
            run.add_dependency(self.dependency)
            with run.span("memory:read", SpanKind.MEMORY):
                hits: list[MemoryHit] = []
                for store in self._distinct_stores():
                    store_kinds = [k for k in wanted if self._stores.get(k) is store]
                    if store_kinds:
                        hits.extend(await store.search(query, kinds=store_kinds, limit=limit))
                hits.sort(key=lambda h: h.score, reverse=True)
                hits = hits[:limit]
                run.emit(
                    EventType.MEMORY_READ,
                    {
                        "memory": self.name,
                        "query": query,
                        "kinds": [k.value for k in wanted],
                        "limit": limit,
                        "hits": [
                            {
                                "id": h.item.id,
                                "kind": h.item.kind.value,
                                "score": h.score,
                                "content": h.item.content,
                            }
                            for h in hits
                        ],
                    },
                )
                return hits

    def recall_sync(self, query: str, **kwargs: Any) -> list[MemoryHit]:
        return run_sync(self.recall(query, **kwargs))

    async def items(self, kind: MemoryKind | str, *, limit: int | None = None) -> list[MemoryItem]:
        kind = MemoryKind(kind)
        return await self.store_for(kind).list_items(kinds=[kind], limit=limit)

    # Context integration ----------------------------------------------------------
    def as_context_source(
        self, *, kinds: Sequence[MemoryKind | str] | None = None, limit: int = 5
    ) -> MemorySource:
        return MemorySource(self, kinds=kinds, limit=limit)

    def _distinct_stores(self) -> list[MemoryStore]:
        seen: list[MemoryStore] = []
        for store in self._stores.values():
            if all(store is not s for s in seen):
                seen.append(store)
        return seen


class MemorySource:
    """Context source that recalls memory relevant to the query."""

    def __init__(
        self,
        memory: Memory,
        *,
        kinds: Sequence[MemoryKind | str] | None = None,
        limit: int = 5,
    ) -> None:
        self.memory = memory
        self.kinds = kinds
        self.limit = limit
        self.name = f"memory:{memory.name}"

    async def fetch(self, query: str | None) -> Sequence[ContextItem]:
        if not query:
            return []
        hits = await self.memory.recall(query, kinds=self.kinds, limit=self.limit)
        return [hit.item.to_context_item(relevance=hit.score) for hit in hits]
