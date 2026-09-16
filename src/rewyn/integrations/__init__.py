"""Third-party integrations (spec §51).

Every module here imports its SDK lazily and none is required by the core
install, so `pip install rewyn` stays lean and provider-agnostic.

| Module | What it connects | Extra |
| --- | --- | --- |
| `otel` | OpenTelemetry traces | `otel` |
| `redis` | Shared memory and checkpoints | `redis` |
| `s3` | Object storage for run payloads | `s3` |
| `search` | Elasticsearch / OpenSearch retrieval | `search` |
| `pgvector` | PostgreSQL vector search | none |
| `frameworks` | LangGraph, CrewAI, LlamaIndex and friends | none |
| `a2a` | Agent-to-agent calls | `remote` |
"""

from __future__ import annotations

import importlib

_MODULES = frozenset({"a2a", "frameworks", "otel", "pgvector", "redis", "s3", "search"})

__all__ = ["a2a", "frameworks", "otel", "pgvector", "redis", "s3", "search"]


def __getattr__(name: str) -> object:
    if name in _MODULES:
        return importlib.import_module(f"rewyn.integrations.{name}")
    raise AttributeError(f"module 'rewyn.integrations' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(_MODULES)
