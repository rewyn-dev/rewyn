"""A provider client must not outlive the event loop it was built on.

``run_sync`` opens a loop per call and closes it again. An async client
caches a connection pool bound to that loop, so an adapter that memoises one
client for its lifetime fails the second time it is used from synchronous
code: ``RuntimeError: Event loop is closed``. In practice that broke the most
ordinary thing a user writes::

    for question in questions:
        agent.run(question)

Every adapter test uses a fake client with no pool, so nothing caught it.
These tests hold the contract directly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rewyn.core.sync import LoopBoundCache, run_sync


class LoopBoundClient:
    """Stands in for httpx: usable only from the loop that created it."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()

    def use(self) -> str:
        if self.loop.is_closed():
            raise RuntimeError("Event loop is closed")
        if self.loop is not asyncio.get_running_loop():
            raise RuntimeError("Future attached to a different loop")
        return "ok"


def test_same_loop_reuses_one_client() -> None:
    """Within a loop the client is built once, not per call."""
    builds = 0

    def factory() -> object:
        nonlocal builds
        builds += 1
        return object()

    cache = LoopBoundCache(factory)

    async def main() -> tuple[object, object]:
        return cache.get(), cache.get()

    first, second = asyncio.run(main())
    assert first is second
    assert builds == 1


def test_each_loop_gets_its_own_client() -> None:
    """A second run_sync call must not reuse the first loop's client."""
    cache = LoopBoundCache(LoopBoundClient)

    async def use() -> str:
        return cache.get().use()

    # This is the exact shape of `agent.run(...)` twice over.
    assert run_sync(use()) == "ok"
    assert run_sync(use()) == "ok"
    assert run_sync(use()) == "ok"


def test_injected_client_is_never_rebuilt() -> None:
    """An explicitly supplied client is the caller's to manage."""
    sentinel = object()

    def factory() -> object:  # pragma: no cover - must never be called
        raise AssertionError("factory called for an injected client")

    cache = LoopBoundCache(factory)
    cache.set(sentinel)

    async def main() -> object:
        return cache.get()

    assert cache.get() is sentinel
    assert asyncio.run(main()) is sentinel


def test_pop_hands_back_the_client_for_closing() -> None:
    """aclose() needs the live client, and only once."""
    cache = LoopBoundCache(LoopBoundClient)

    async def main() -> tuple[Any, Any]:
        built = cache.get()
        return built, cache.pop()

    built, popped = asyncio.run(main())
    assert popped is built
    assert asyncio.run(_pop_again(cache)) is None


async def _pop_again(cache: LoopBoundCache) -> Any:
    return cache.pop()
