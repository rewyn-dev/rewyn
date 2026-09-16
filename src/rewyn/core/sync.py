"""Bridge between the async-first core and the synchronous facade API.

The spec shows synchronous entry points (``agent.run(...)``,
``model.generate(...)``). Internally everything is async. ``run_sync`` runs a
coroutine to completion from synchronous code, including from inside a
running event loop (notebooks, frameworks) by using a dedicated worker
thread with its own loop. Context variables are propagated so the active
run and span are visible inside the coroutine.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import queue
import threading
from collections.abc import AsyncIterator, Coroutine, Iterator
from typing import Any, TypeVar

T = TypeVar("T")


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` to completion and return its result.

    Safe to call whether or not an event loop is already running in the
    current thread.
    """
    ctx = contextvars.copy_context()
    if not _loop_running():
        return ctx.run(asyncio.run, coro)

    result: list[T] = []
    error: list[BaseException] = []

    def _worker() -> None:
        try:
            result.append(ctx.run(asyncio.run, coro))
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=_worker, name="rewyn-run-sync", daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


def iterate_sync(agen: AsyncIterator[T]) -> Iterator[T]:
    """Consume an async iterator from synchronous code, yielding items as they arrive.

    The async iterator runs on a dedicated thread with its own event loop;
    items cross to the caller through a queue so streaming stays incremental.
    """
    ctx = contextvars.copy_context()
    items: queue.Queue[tuple[str, Any]] = queue.Queue()

    async def _pump() -> None:
        try:
            async for item in agen:
                items.put(("item", item))
        except BaseException as exc:
            items.put(("error", exc))
        finally:
            items.put(("done", None))

    thread = threading.Thread(
        target=lambda: ctx.run(asyncio.run, _pump()), name="rewyn-iterate-sync", daemon=True
    )
    thread.start()
    while True:
        kind, value = items.get()
        if kind == "item":
            yield value
        elif kind == "error":
            thread.join()
            raise value
        else:
            thread.join()
            return


class BackgroundLoop:
    """An event loop running on a daemon thread, for sync facades over async resources.

    Used by synchronous MCP connections: the connection lives on this loop,
    and calls from other loops or threads are submitted to it.
    """

    def __init__(self, name: str = "rewyn-background-loop") -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name=name, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        return self.submit(coro).result(timeout)

    async def call(self, coro: Coroutine[Any, Any, T]) -> T:
        """Await ``coro`` on this loop from any other running loop."""
        return await asyncio.wrap_future(self.submit(coro))

    def stop(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5.0)
        if not self.loop.is_running():
            self.loop.close()
