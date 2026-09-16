"""Process-local memory store."""

from __future__ import annotations

from collections.abc import Sequence

from rewyn.memory.memory import MemoryHit, MemoryItem, MemoryKind, lexical_score


class InMemoryStore:
    name = "in_memory"

    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}

    async def add(self, item: MemoryItem) -> MemoryItem:
        self._items[item.id] = item
        return item

    async def get(self, item_id: str) -> MemoryItem | None:
        return self._items.get(item_id)

    async def delete(self, item_id: str) -> bool:
        return self._items.pop(item_id, None) is not None

    async def list_items(
        self, *, kinds: Sequence[MemoryKind] | None = None, limit: int | None = None
    ) -> list[MemoryItem]:
        items = [i for i in self._items.values() if kinds is None or i.kind in kinds]
        items.sort(key=lambda i: i.updated_at, reverse=True)
        return items[:limit] if limit else items

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
        if kinds is None:
            count = len(self._items)
            self._items.clear()
            return count
        doomed = [k for k, v in self._items.items() if v.kind in kinds]
        for key in doomed:
            del self._items[key]
        return len(doomed)
