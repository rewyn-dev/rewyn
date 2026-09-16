"""Primitive types shared by every Rewyn subsystem."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from typing import Any, Protocol, TypeAlias, runtime_checkable

JSONValue: TypeAlias = Any
"""A JSON-compatible value. Kept as ``Any`` for ergonomics; validated at the edges."""

JSONObject: TypeAlias = dict[str, Any]

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_base32(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def ulid() -> str:
    """Return a 26-character, lexicographically sortable ULID."""
    timestamp_ms = int(time.time() * 1000)
    randomness = secrets.randbits(80)
    return _encode_base32(timestamp_ms, 10) + _encode_base32(randomness, 16)


def new_id(prefix: str) -> str:
    """Return a prefixed, time-sortable identifier such as ``run_01J...``."""
    return f"{prefix}_{ulid()}"


def utcnow() -> datetime:
    """Timezone-aware current time. All Rewyn timestamps are UTC."""
    return datetime.now(tz=UTC)


@runtime_checkable
class Versioned(Protocol):
    """An object whose exact configuration can be reconstructed later.

    Every versionable object (prompt, skill, tool, agent, graph, context
    configuration, MCP configuration, dataset, policy) exposes a human
    ``version`` and a content-derived ``fingerprint``.
    """

    @property
    def version(self) -> str: ...

    def fingerprint(self) -> str: ...


class RewynError(Exception):
    """Base class for all Rewyn errors."""


class ConfigurationError(RewynError):
    """A primitive was configured incorrectly."""


class BudgetExceededError(RewynError):
    """A loop, agent or context exceeded a configured budget."""


class MissingDependencyError(RewynError):
    """An optional dependency is required for the requested feature."""

    def __init__(self, package: str, extra: str) -> None:
        super().__init__(
            f"The '{package}' package is required for this feature. "
            f"Install it with: pip install 'rewyn[{extra}]'"
        )
        self.package = package
        self.extra = extra
