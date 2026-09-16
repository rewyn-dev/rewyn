"""Context items, provenance and trust metadata (spec §7, §9, §39).

A context is an engineered object, not a string. Every item carries where it
came from, how much it can be trusted, how sensitive it is, and the signals
the budget allocator uses to decide what deserves space.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.schema import fingerprint
from rewyn.core.types import JSONObject, new_id, utcnow


class ContextKind(StrEnum):
    """Sections of a context, in the order they are rendered (spec §7)."""

    INSTRUCTIONS = "instructions"
    USER = "user"
    KNOWLEDGE = "knowledge"
    MEMORY = "memory"
    TOOLS = "tools"
    SKILLS = "skills"
    STATE = "state"
    EXAMPLES = "examples"
    RUNTIME = "runtime"
    HISTORY = "history"


KIND_ORDER: dict[ContextKind, int] = {kind: i for i, kind in enumerate(ContextKind)}


class TrustLevel(StrEnum):
    SYSTEM = "system"
    TRUSTED = "trusted"
    INTERNAL = "internal"
    UNTRUSTED = "untrusted"

    @property
    def rank(self) -> int:
        return {
            TrustLevel.SYSTEM: 3,
            TrustLevel.TRUSTED: 2,
            TrustLevel.INTERNAL: 1,
            TrustLevel.UNTRUSTED: 0,
        }[self]


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Provenance(BaseModel):
    """Where a context item came from (spec §9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    record: str | None = None
    version: str | None = None
    uri: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    hash: str | None = None
    authority: float = 0.5

    @classmethod
    def for_content(cls, source: str, content: str, **kwargs: Any) -> Provenance:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return cls(source=source, hash=f"sha256:{digest}", **kwargs)

    def verify(self, content: str) -> bool:
        if self.hash is None:
            return True
        return self.hash == f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("ctx"))
    kind: ContextKind = ContextKind.KNOWLEDGE
    content: str
    title: str | None = None
    tokens: int | None = None
    # Priority signals (spec §8), each in [0, 1].
    relevance: float = 0.5
    authority: float | None = None
    user_importance: float = 0.5
    task_importance: float = 0.5
    confidence: float = 0.5
    recency: datetime | None = None
    required: bool = False
    # Security (spec §39).
    trust_level: TrustLevel = TrustLevel.INTERNAL
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    permissions: list[str] = Field(default_factory=list)
    provenance: Provenance | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: JSONObject = Field(default_factory=dict)

    @property
    def effective_authority(self) -> float:
        if self.authority is not None:
            return self.authority
        if self.provenance is not None:
            return self.provenance.authority
        return 0.5

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:16]

    def fingerprint(self) -> str:
        return fingerprint(
            {
                "kind": self.kind.value,
                "content": self.content,
                "title": self.title,
                "trust_level": self.trust_level.value,
                "provenance": self.provenance.model_dump(mode="json", exclude={"retrieved_at"})
                if self.provenance
                else None,
            }
        )

    def summary(self) -> JSONObject:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "tokens": self.tokens,
            "trust_level": self.trust_level.value,
            "sensitivity": self.sensitivity.value,
            "source": self.provenance.source if self.provenance else None,
            "hash": self.content_hash,
        }


@runtime_checkable
class ContextSource(Protocol):
    """Anything that can contribute context items for a query."""

    name: str

    async def fetch(self, query: str | None) -> Sequence[ContextItem]: ...


class StaticSource:
    """A fixed list of items (policies, examples, instructions)."""

    def __init__(self, items: Iterable[ContextItem], *, name: str = "static") -> None:
        self.name = name
        self.items = list(items)

    async def fetch(self, query: str | None) -> Sequence[ContextItem]:
        return list(self.items)


class CallableSource:
    """Wrap a sync or async function ``(query) -> items``."""

    def __init__(
        self,
        fn: Callable[[str | None], Sequence[ContextItem] | Awaitable[Sequence[ContextItem]]],
        *,
        name: str | None = None,
    ) -> None:
        self.fn = fn
        self.name = name or getattr(fn, "__name__", "callable")

    async def fetch(self, query: str | None) -> Sequence[ContextItem]:
        result = self.fn(query)
        if inspect.isawaitable(result):
            return list(await result)
        return list(result)


def text_item(
    content: str,
    *,
    kind: ContextKind | str = ContextKind.KNOWLEDGE,
    source: str = "inline",
    **kwargs: Any,
) -> ContextItem:
    """Convenience constructor with content-hash provenance."""
    return ContextItem(
        kind=ContextKind(kind),
        content=content,
        provenance=Provenance.for_content(source, content),
        **kwargs,
    )
