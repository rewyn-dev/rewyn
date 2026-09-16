"""Embedding providers.

``HashingEmbedder`` is deterministic and dependency-free (feature hashing of
word unigrams and bigrams), which makes RAG tests and offline demos
reproducible. ``OpenAIEmbedder`` uses the OpenAI embeddings API via the
optional ``openai`` extra.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from itertools import pairwise
from typing import Any, Protocol, runtime_checkable

from rewyn.core.types import MissingDependencyError

_WORD = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    name: str
    dimension: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def embedding_units(texts: Sequence[str]) -> int:
    """A rough token count for pricing an embedding batch (spec §40)."""
    return max(1, sum(len(t) for t in texts) // 4)


def record_embedding_cost(embedder_name: str, texts: Sequence[str]) -> float:
    """Charge the active run for one embedding batch. Unpriced embedders cost nothing."""
    from rewyn.core.run import current_run
    from rewyn.models.pricing import compute_unit_cost

    cost = compute_unit_cost("embedding", embedder_name, calls=1, units=embedding_units(texts))
    run = current_run()
    if cost and run is not None:
        run.record_cost("embedding", cost)
    return cost


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class HashingEmbedder:
    name = "hashing"

    def __init__(self, dimension: int = 256) -> None:
        self.dimension = dimension

    def _vector(self, text: str) -> list[float]:
        words = _WORD.findall(text.lower())
        features = words + [f"{a}_{b}" for a, b in pairwise(words)]
        vector = [0.0] * self.dimension
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OpenAIEmbedder:
    name = "openai"

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        *,
        dimension: int = 1536,
        client: Any | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.dimension = dimension
        self._client = client
        self._api_key = api_key

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover
                raise MissingDependencyError("openai", "openai") from exc
            kwargs: dict[str, Any] = {"api_key": self._api_key} if self._api_key else {}
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(model=self.model, input=list(texts))
        return [list(item.embedding) for item in response.data]
