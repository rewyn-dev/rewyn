"""JSONL-backed memory store under ``.rewyn/memory/<namespace>.jsonl``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from rewyn.core.settings import get_settings
from rewyn.memory.memory import MemoryHit, MemoryItem, MemoryKind, lexical_score
from rewyn.security.redaction import default_redactor
from rewyn.storage.local import atomic_write_text


class FileStore:
    name = "file"

    def __init__(self, path: Path | None = None, *, namespace: str = "default") -> None:
        self.namespace = namespace
        self.path = Path(path) if path else get_settings().memory_dir / f"{namespace}.jsonl"
        self._explicit_path = path is not None
        self._items: dict[str, MemoryItem] | None = None
        self._lock = asyncio.Lock()

    def for_namespace(self, namespace: str) -> FileStore:
        """A store for another tenant: a separate file, not a filtered view.

        When an explicit path was given the file is shared, so fall back to
        filtering rather than silently writing every tenant to one file.
        """
        if namespace == self.namespace:
            return self
        if self._explicit_path:
            from rewyn.memory.memory import NamespacedStore

            return NamespacedStore(self, namespace)  # type: ignore[return-value]
        return FileStore(namespace=namespace)

    # Persistence ---------------------------------------------------------------
    def _load(self) -> dict[str, MemoryItem]:
        if self._items is None:
            self._items = {}
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        item = MemoryItem.model_validate_json(line)
                        self._items[item.id] = item
        return self._items

    def _save(self) -> None:
        items = self._load()
        redactor = default_redactor()
        lines = [
            json.dumps(redactor.redact(item.model_dump(mode="json")), ensure_ascii=False)
            for item in items.values()
        ]
        atomic_write_text(self.path, "\n".join(lines) + ("\n" if lines else ""))

    # MemoryStore -----------------------------------------------------------------
    async def add(self, item: MemoryItem) -> MemoryItem:
        async with self._lock:
            self._load()[item.id] = item
            self._save()
        return item

    async def get(self, item_id: str) -> MemoryItem | None:
        return self._load().get(item_id)

    async def delete(self, item_id: str) -> bool:
        async with self._lock:
            removed = self._load().pop(item_id, None) is not None
            if removed:
                self._save()
        return removed

    async def list_items(
        self, *, kinds: Sequence[MemoryKind] | None = None, limit: int | None = None
    ) -> list[MemoryItem]:
        items = [i for i in self._load().values() if kinds is None or i.kind in kinds]
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
        async with self._lock:
            items = self._load()
            doomed = [k for k, v in items.items() if kinds is None or v.kind in kinds]
            for key in doomed:
                del items[key]
            self._save()
        return len(doomed)
