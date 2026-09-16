"""Explicit, versioned state (spec §20).

``State`` is a mutable mapping that keeps an immutable, fingerprinted
snapshot for every version. Graph execution, checkpoints and replay build on
it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from copy import deepcopy
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rewyn.core.schema import fingerprint, to_jsonable
from rewyn.core.types import JSONObject, utcnow


class StateSnapshot(BaseModel):
    """Immutable copy of the state at a given version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    data: JSONObject
    fingerprint: str
    created_at: datetime = Field(default_factory=utcnow)
    reason: str | None = None


class State(MutableMapping[str, Any]):
    """Mutable mapping with versioned, immutable snapshots."""

    def __init__(self, initial: Mapping[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(initial or {})
        self._version = 0
        self._history: list[StateSnapshot] = [self._make_snapshot("initial")]

    # MutableMapping protocol -------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._bump(f"set {key}")

    def __delitem__(self, key: str) -> None:
        del self._data[key]
        self._bump(f"delete {key}")

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"State(version={self._version}, data={self._data!r})"

    # Versioning --------------------------------------------------------------
    @property
    def version(self) -> int:
        return self._version

    @property
    def history(self) -> list[StateSnapshot]:
        return list(self._history)

    def update(self, other: Any = (), /, **kwargs: Any) -> None:
        """Apply several changes as one version bump."""
        self._data.update(other, **kwargs)
        self._bump("update")

    def snapshot(self, reason: str | None = None) -> StateSnapshot:
        """Return the snapshot of the current version (creating it if needed)."""
        latest = self._history[-1]
        if latest.version == self._version and latest.reason == reason:
            return latest
        return self._make_snapshot(reason)

    def restore(self, snapshot: StateSnapshot) -> None:
        """Replace the contents with ``snapshot`` and record a new version."""
        self._data = deepcopy(snapshot.data)
        self._bump(f"restore v{snapshot.version}")

    def to_dict(self) -> JSONObject:
        data: JSONObject = to_jsonable(self._data)
        return data

    # Internals ---------------------------------------------------------------
    def _bump(self, reason: str) -> None:
        self._version += 1
        self._history.append(self._make_snapshot(reason))

    def _make_snapshot(self, reason: str | None) -> StateSnapshot:
        data: JSONObject = to_jsonable(self._data)
        return StateSnapshot(
            version=self._version, data=data, fingerprint=fingerprint(data), reason=reason
        )
