"""Recording sampling (spec §53).

At low volume you record everything. At high volume you cannot: a service
doing a million runs a day does not want a million event logs, and the
bill for storing them is real.

Sampling here is **per run, not per event**. Half the events of a run are
useless: you cannot replay them, diff them or evaluate them. So the decision
is made once when the run starts and every event of that run follows it,
which keeps every retained run complete and replayable.

Two things are never dropped, because they are the runs you actually go
looking for:

- runs that failed
- runs explicitly marked with :func:`always_record`

Configure with ``REWYN_SAMPLE_RATE`` (0.0 to 1.0), or pass a sampler.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from rewyn.core.event import Event, EventType

ALWAYS_TAG = "always-record"

_TERMINAL_FAILURES = frozenset({EventType.RUN_FAILED, EventType.RUN_CANCELLED})


@runtime_checkable
class Sampler(Protocol):
    """Decides whether one run is recorded."""

    name: str

    def sample(self, run_id: str, *, tags: frozenset[str] = frozenset()) -> bool: ...


class AlwaysSample:
    """Record everything. The default, and the right choice until it is not."""

    name = "always"

    def sample(self, run_id: str, *, tags: frozenset[str] = frozenset()) -> bool:
        return True


class NeverSample:
    """Record nothing except what is forced. Useful for load tests."""

    name = "never"

    def sample(self, run_id: str, *, tags: frozenset[str] = frozenset()) -> bool:
        return ALWAYS_TAG in tags


class RateSampler:
    """Record a deterministic fraction of runs.

    The decision is a hash of the run id rather than a coin flip, so it is
    reproducible: the same run id always samples the same way, and two
    processes agree without coordinating.
    """

    name = "rate"

    def __init__(self, rate: float) -> None:
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"sample rate must be between 0 and 1, got {rate}")
        self.rate = rate

    def __repr__(self) -> str:
        return f"RateSampler({self.rate})"

    def sample(self, run_id: str, *, tags: frozenset[str] = frozenset()) -> bool:
        if ALWAYS_TAG in tags or self.rate >= 1.0:
            return True
        if self.rate <= 0.0:
            return False
        digest = hashlib.blake2b(run_id.encode("utf-8"), digest_size=8).digest()
        return (int.from_bytes(digest, "big") % 1_000_000) < self.rate * 1_000_000


def always_record(tags: list[str]) -> list[str]:
    """Add the tag that exempts a run from sampling."""
    return [*tags, ALWAYS_TAG] if ALWAYS_TAG not in tags else list(tags)


def is_failure(event: Event) -> bool:
    """True for the terminal events that must survive sampling."""
    return event.type in _TERMINAL_FAILURES


def sampler_from_settings() -> Sampler:
    """Build the sampler described by ``REWYN_SAMPLE_RATE``."""
    from rewyn.core.settings import get_settings

    rate = get_settings().sample_rate
    return AlwaysSample() if rate >= 1.0 else RateSampler(rate)
